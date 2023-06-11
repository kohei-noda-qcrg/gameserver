import uuid
from enum import IntEnum

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import NoResultFound

from .db import engine


class InvalidToken(Exception):
    """指定されたtokenが不正だったときに投げるエラー"""


class SafeUser(BaseModel):
    """token を含まないUser"""

    id: int
    name: str
    leader_card_id: int

    # SafeUser.from_orm(row) できるようにする
    class Config:
        orm_mode = True


class RoomInfo(BaseModel):
    room_id: int
    live_id: int
    joined_user_count: int
    max_user_count: int

    class Config:
        orm_mode = True


def create_user(name: str, leader_card_id: int) -> str:
    """Create new user and returns their token"""
    # UUID4は天文学的な確率だけど衝突する確率があるので、気にするならリトライする必要がある。
    # サーバーでリトライしない場合は、クライアントかユーザー（手動）にリトライさせることになる。
    # ユーザーによるリトライは一般的には良くないけれども、確率が非常に低ければ許容できる場合もある。
    token = str(uuid.uuid4())
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "INSERT INTO `user` (name, token, leader_card_id)"
                " VALUES (:name, :token, :leader_card_id)"
            ),
            {"name": name, "token": token, "leader_card_id": leader_card_id},
        )
        print(f"create_user(): {result.lastrowid=}")  # DB側で生成されたPRIMARY KEYを参照できる
    return token


def _get_user_by_token(conn, token: str) -> SafeUser | None:
    result = conn.execute(
        text("SELECT `id`, `name`, `leader_card_id` FROM `user` WHERE `token`=:token"),
        {"token": token},
    )
    try:
        row = result.one()
    except NoResultFound:
        return None
    return SafeUser.from_orm(row)


def get_user_by_token(token: str) -> SafeUser | None:
    with engine.begin() as conn:
        return _get_user_by_token(conn, token)


def update_user(token: str, name: str, leader_card_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE `user` SET `name`=:name, `leader_card_id`=:leader_card_id"
                " WHERE `token`=:token"
            ),
            {"token": token, "name": name, "leader_card_id": leader_card_id},
        )


# IntEnum の使い方の例
class LiveDifficulty(IntEnum):
    """難易度"""

    normal = 1
    hard = 2


class JoinRoomResult(IntEnum):
    Ok = 1
    RoomFull = 2
    Disbanded = 3
    OtherError = 4


# class WaitRoomStatus(IntEnum):
#     Waiting = 1
#     LiveStart = 2
#     Dissolution = 3


def create_room(token: str, live_id: int, difficulty: LiveDifficulty) -> int:
    """部屋を作ってroom_idを返します"""
    with engine.begin() as conn:
        user = _get_user_by_token(conn, token)
        if user is None:
            raise InvalidToken
        result = conn.execute(
            text(
                "INSERT INTO `room` SET `live_id`=:live_id, `leader_id`=:leader_id, `joined_user_count`=:joined_user_count"
                ", `max_user_count`=:max_user_count"
            ),
            {
                "live_id": live_id,
                "leader_id": user.id,
                "joined_user_count": 1,
                "max_user_count": 4,
            },
        )
        conn.execute(
            text(
                "INSERT INTO `room_user` SET `room_id`=:room_id, `user_id`=:user_id, `difficulty`=:difficulty"
            ),
            {
                "room_id": result.lastrowid,
                "user_id": user.id,
                "difficulty": difficulty.value,
            },
        )
    return result.lastrowid


def room_search(live_id: int) -> list[RoomInfo]:
    with engine.begin() as conn:
        if live_id == 0:  # Wildcard (search all rooms)
            result = conn.execute(
                text(
                    "SELECT `room_id`, `live_id`, `joined_user_count`, `max_user_count` FROM `room`"
                )
            )
        else:
            result = conn.execute(
                text(
                    "SELECT `room_id`, `live_id`, `joined_user_count`, `max_user_count` FROM `room` WHERE `live_id`=:live_id"
                ),
                {"live_id": live_id},
            )
        rows = result.all()
        rows = [RoomInfo.from_orm(row) for row in rows]
        return rows


def join_room(token: str, room_id: int, select_difficulty: LiveDifficulty) -> int:
    with engine.begin() as conn:
        user = _get_user_by_token(conn, token)
        if user is None:
            raise InvalidToken

        result = conn.execute(
            text(
                "SELECT `joined_user_count`, `max_user_count` FROM `room` WHERE `room_id`=:room_id"
                " FOR UPDATE"
            ),
            {"room_id": room_id},
        )
        row = result.one()
        if row.joined_user_count >= row.max_user_count:
            return JoinRoomResult.RoomFull

        conn.execute(
            text(
                "INSERT INTO `room_user` SET `room_id`=:room_id, `user_id`=:user_id, `difficulty`=:difficulty"
            ),
            {
                "room_id": room_id,
                "user_id": user.id,
                "difficulty": select_difficulty.value,
            },
        )
        conn.execute(
            text(
                "UPDATE `room` SET `joined_user_count`=`joined_user_count`+1 WHERE `room_id`=:room_id"
            ),
            {"room_id": room_id},
        )
    return JoinRoomResult.Ok
