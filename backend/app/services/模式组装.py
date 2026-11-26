"""模式组装 —— 骨签：旧模式字段序不得被新模式重排。

两套模式并存：
  甲式（旧）字段序固定，声明为甲式的正文与页面写都保持甲序，组装器绝不按新序重排；
  新式只作用于声明为新式的正文。

工程形态：
  - 模式声明收成“不可变版本快照”（模式声明表，每次改模式新增一行、版本自增），
    已组装投递在组装时刻冻结其模式/字段序/正文，之后改声明不回刷旧序；
  - 退役模式的投递塞进只读归档轨，组装器的现行读集合只绑现行模式；
  - 旧序、新序双写两轨，旧模式投递不进新序集合；
  - 进出两向并发改同一条声明，由进程内锁串行化，杜绝字段序互相覆盖的撕裂态。
"""
from __future__ import annotations
import json
import threading
import uuid

from app.db import 连接
from app.services.时间 import 此刻

# 两套并存模式的规范字段序（正文 key 序）。甲序即旧序，永不被新序重排。
甲序 = ['事件号', '事件类型', '端点键', '载荷摘要', '优先级']
新序 = ['事件号', '优先级', '载荷摘要', '事件类型', '端点键']
规范序 = {'甲式': 甲序, '新式': 新序}

# 串行化声明/组装/退役，防止两路并发改同一条声明时字段序互相覆盖
_锁 = threading.RLock()


def dumps(obj) -> str:
    """保序序列化：dict 按插入顺序落 JSON，字段序即 key 序。"""
    return json.dumps(obj, ensure_ascii=False)


def loads(s: str):
    return json.loads(s)


def 按序(序: list, 正文: dict) -> dict:
    """按给定字段序重排正文；规范序外的剩余键原样追加在末尾，不丢字段。"""
    出 = {}
    for k in 序:
        if k in 正文:
            出[k] = 正文[k]
    for k, v in 正文.items():
        if k not in 出:
            出[k] = v
    return 出


def _现行声明行(conn, 来源号: str):
    return conn.execute(
        "SELECT * FROM 模式声明 WHERE 来源号=? ORDER BY 版本 DESC LIMIT 1",
        (来源号,),
    ).fetchone()


def 声明模式(来源号: str, 模式: str) -> dict:
    """对某来源声明模式，收成不可变版本快照。重复声明同模式不产生新版本。"""
    if 模式 not in 规范序:
        raise ValueError(f"未知模式：{模式}（仅支持 甲式/新式）")
    t = 此刻()
    with _锁:
        conn = 连接()
        try:
            行 = _现行声明行(conn, 来源号)
            if 行 and 行['模式'] == 模式 and int(行['是否退役']) == 0:
                return _声明dict(行)
            新版本 = (int(行['版本']) + 1) if 行 else 1
            conn.execute(
                "INSERT INTO 模式声明(来源号,版本,模式,字段顺序,是否退役,时刻) VALUES (?,?,?,?,0,?)",
                (来源号, 新版本, 模式, dumps(规范序[模式]), t),
            )
            conn.commit()
            return _声明dict(_现行声明行(conn, 来源号))
        finally:
            conn.close()


def _声明dict(行) -> dict:
    return {
        '来源号': 行['来源号'],
        '版本': int(行['版本']),
        '模式': 行['模式'],
        '字段顺序': loads(行['字段顺序']),
        '是否退役': bool(int(行['是否退役'])),
        '时刻': 行['时刻'],
    }


def 取声明(来源号: str) -> dict:
    conn = 连接()
    try:
        行 = _现行声明行(conn, 来源号)
        if not 行:
            raise ValueError(f"来源未声明模式：{来源号}")
        return _声明dict(行)
    finally:
        conn.close()


def 组装(来源号: str, 正文: dict) -> dict:
    """按该来源“现行声明快照”的模式组装一条投递，并冻结为不可变快照。

    甲式：保持甲序，绝不按新序重排；新式：按新序落。旧正文拒绝回刷成新序。
    """
    t = 此刻()
    with _锁:
        conn = 连接()
        try:
            行 = _现行声明行(conn, 来源号)
            if not 行:
                raise ValueError(f"来源未声明模式，拒绝组装：{来源号}")
            模式 = 行['模式']
            版本 = int(行['版本'])
            序 = 规范序[模式]
            # 关键：甲式永远按甲序落，绝不采用新序；新式才按新序
            冻结正文 = 按序(序, 正文)
            投递号 = f"DLV-CO-{uuid.uuid4().hex[:12]}"
            conn.execute(
                "INSERT INTO 模式投递(投递号,来源号,模式版本,模式,字段顺序,正文JSON,归档,时刻) "
                "VALUES (?,?,?,?,?,?,0,?)",
                (投递号, 来源号, 版本, 模式, dumps(序), dumps(冻结正文), t),
            )
            conn.commit()
            return {
                '投递号': 投递号,
                '来源号': 来源号,
                '模式版本': 版本,
                '模式': 模式,
                '字段顺序': 序,
                '正文': 冻结正文,
                '时刻': t,
            }
        finally:
            conn.close()


def _投递dict(行) -> dict:
    return {
        '投递号': 行['投递号'],
        '来源号': 行['来源号'],
        '模式版本': int(行['模式版本']),
        '模式': 行['模式'],
        '字段顺序': loads(行['字段顺序']),
        '正文': loads(行['正文JSON']),
        '归档': bool(int(行['归档'])),
        '时刻': 行['时刻'],
    }


def 投递详情(投递号: str) -> dict:
    """单条投递详情：显式列出“字段顺序”本身，并带模式版本与冻结正文。"""
    conn = 连接()
    try:
        行 = conn.execute("SELECT * FROM 模式投递 WHERE 投递号=?", (投递号,)).fetchone()
        if not 行:
            raise ValueError(f"无此模式投递：{投递号}")
        return _投递dict(行)
    finally:
        conn.close()


def 页面字段序(投递号: str) -> list:
    """页面写该投递时应采用的字段序：甲式保持甲序，不按新序渲染。"""
    return 投递详情(投递号)['字段顺序']


def 读集合(模式: str | None) -> list:
    """现行读集合，按模式分轨。模式为 None 时返回全部非归档投递。

    旧模式投递不进新序集合；归档（退役）投递不在任何现行读集合内。
    """
    conn = 连接()
    try:
        if 模式 is None:
            rows = conn.execute(
                "SELECT * FROM 模式投递 WHERE 归档=0 ORDER BY 时刻 DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM 模式投递 WHERE 归档=0 AND 模式=? ORDER BY 时刻 DESC",
                (模式,),
            ).fetchall()
        return [_投递dict(r) for r in rows]
    finally:
        conn.close()


def 归档轨(模式: str) -> list:
    """退役模式的只读归档轨。"""
    conn = 连接()
    try:
        rows = conn.execute(
            "SELECT * FROM 模式投递 WHERE 归档=1 AND 模式=? ORDER BY 时刻 DESC",
            (模式,),
        ).fetchall()
        return [_投递dict(r) for r in rows]
    finally:
        conn.close()


def 退役模式(模式: str) -> dict:
    """退役某模式：其投递塞只读归档轨；组装器现行读集合改绑其余现行模式。幂等。"""
    if 模式 not in 规范序:
        raise ValueError(f"未知模式：{模式}")
    t = 此刻()
    with _锁:
        conn = 连接()
        try:
            退役来源 = f"__退役__:{模式}"
            旧 = conn.execute(
                "SELECT MAX(版本) AS v FROM 模式声明 WHERE 来源号=?", (退役来源,)
            ).fetchone()["v"]
            conn.execute(
                "INSERT INTO 模式声明(来源号,版本,模式,字段顺序,是否退役,时刻) "
                "VALUES (?,?,?,?,1,?)",
                (退役来源, (int(旧) + 1) if 旧 else 1, 模式, dumps(规范序[模式]), t),
            )
            cur = conn.execute(
                "UPDATE 模式投递 SET 归档=1 WHERE 模式=? AND 归档=0", (模式,)
            )
            conn.commit()
            return {'退役模式': 模式, '归档条数': cur.rowcount, '时刻': t}
        finally:
            conn.close()


def 改写字段序(投递号: str, 新字段序: list) -> dict:
    """尝试改写某投递字段序。归档（只读）投递一律拒绝，旧序拒绝回刷成新序。"""
    _ = 新字段序  # 任何目标序都不允许：字段序是组装时刻的不可变快照
    conn = 连接()
    try:
        行 = conn.execute("SELECT * FROM 模式投递 WHERE 投递号=?", (投递号,)).fetchone()
        if not 行:
            raise ValueError(f"无此模式投递：{投递号}")
        if int(行['归档']) == 1:
            raise ValueError("归档投递为只读，不得回刷字段序")
        raise ValueError("投递字段序为组装时刻的不可变快照，不得改写")
    finally:
        conn.close()


def 对账() -> dict:
    """联查声明列与正文字段序：影子正文若被重排，必须列红，不得误标仍是原模式。"""
    conn = 连接()
    try:
        rows = conn.execute("SELECT * FROM 模式投递 ORDER BY 时刻 DESC").fetchall()
        明细 = []
        对齐数 = 0
        for r in rows:
            正文 = loads(r['正文JSON'])
            实际序 = list(正文.keys())
            快照序 = loads(r['字段顺序'])
            对齐 = (实际序 == 快照序)
            if 对齐:
                对齐数 += 1
            明细.append({
                '投递号': r['投递号'],
                '来源号': r['来源号'],
                '声明模式': r['模式'],
                '模式版本': int(r['模式版本']),
                '字段顺序': 实际序,          # 对账行列出字段顺序本身
                '实际字段顺序': 实际序,
                '快照字段顺序': 快照序,
                '是否对齐': 对齐,
                '归档': bool(int(r['归档'])),
            })
        总 = len(明细)
        return {
            '总数': 总,
            '对齐数': 对齐数,
            '红数': 总 - 对齐数,
            '是否全部对齐': 对齐数 == 总,
            '明细': 明细,
        }
    finally:
        conn.close()
