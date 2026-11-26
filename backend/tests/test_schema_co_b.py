"""中卷 · test_schema_co_b.py —— 失败测乙：组装对账把被重排的影子正文误标成仍是甲式，列红。

骨签：旧模式字段序不得被新模式重排。
本卷只验“列与归属”：
  1. 对账联查声明列与正文字段序：影子正文若被重排成新序，必须列红，不得仍标甲式；
  2. 旧模式的投递不能进新序集合（双写两轨，读集合按现行模式分轨）；
  3. 退役模式的投递进只读归档轨，不被现行读集合改写。
"""
import os
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ['数据目录'] = _tmp
os.environ['夹具目录'] = str(Path(__file__).resolve().parents[2] / 'fixtures')

from app import seed
from app.db import 连接
from app.factory import create_app
from app.services import 夹具, 模式组装
import json as _json

甲序 = ['事件号', '事件类型', '端点键', '载荷摘要', '优先级']
新序 = ['事件号', '优先级', '载荷摘要', '事件类型', '端点键']


def setup_module():
    seed.初始化库()


def _甲式正文() -> dict:
    return 夹具.读取('模式复现/甲式正文')['事件'][0]


def test_对账识别被重排的影子正文并标红():
    模式组装.声明模式('co-b-1', '甲式')
    投递 = 模式组装.组装('co-b-1', _甲式正文())
    号 = 投递['投递号']
    # 模拟影子正文被人按新序重排（声明列仍写着甲式）
    conn = 连接()
    try:
        被重排 = 模式组装.按序(新序, _甲式正文())
        conn.execute("UPDATE 模式投递 SET 正文JSON=? WHERE 投递号=?",
                     (模式组装.dumps(被重排), 号))
        conn.commit()
    finally:
        conn.close()
    对账 = 模式组装.对账()
    命中 = [r for r in 对账['明细'] if r['投递号'] == 号]
    assert 命中, '对账缺少该投递行'
    行 = 命中[0]
    assert 行['声明模式'] == '甲式'
    assert 行['实际字段顺序'] == 新序
    assert 行['是否对齐'] is False, '列红：被重排的影子正文不得被误标成仍是甲式'
    assert 行['字段顺序'] == 新序  # 对账行同样要列出字段顺序本身


def test_旧模式投递不进新序集合():
    模式组装.声明模式('co-b-2', '甲式')
    甲投递 = 模式组装.组装('co-b-2', _甲式正文())
    模式组装.声明模式('co-b-2n', '新式')
    新正文 = 模式组装.按序(新序, _甲式正文())
    新投递 = 模式组装.组装('co-b-2n', 新正文)
    新轨 = 模式组装.读集合('新式')
    号集 = {r['投递号'] for r in 新轨}
    assert 新投递['投递号'] in 号集
    assert 甲投递['投递号'] not in 号集, '旧模式的投递不能进新序集合'
    # 甲轨读到的仍是甲序
    甲轨 = 模式组装.读集合('甲式')
    甲行 = [r for r in 甲轨 if r['投递号'] == 甲投递['投递号']][0]
    assert 甲行['字段顺序'] == 甲序


def test_退役模式投递进只读归档轨():
    模式组装.声明模式('co-b-3', '甲式')
    归档投递 = 模式组装.组装('co-b-3', _甲式正文())
    号 = 归档投递['投递号']
    # 甲式退役：其投递塞只读归档轨，组装器改绑现行模式（新式）的读集合
    模式组装.退役模式('甲式')
    归档 = 模式组装.归档轨('甲式')
    assert any(r['投递号'] == 号 for r in 归档), '退役投递应进只读归档轨'
    # 现行读集合（新式）不含归档投递
    assert all(r['投递号'] != 号 for r in 模式组装.读集合('新式'))
    # 归档只读：尝试改写归档投递字段序应被拒
    try:
        模式组装.改写字段序(号, 新序)
        raise AssertionError('归档投递不得被回刷改写')
    except ValueError:
        pass
    # 归档里的序仍是甲序
    行 = [r for r in 归档 if r['投递号'] == 号][0]
    assert 行['字段顺序'] == 甲序


def test_http出口原始字节序保持甲式不被jsonify重排():
    # 这条验“响应出口”的顺序：直接对响应原文做 JSON 解析（解析保序），
    # 取 正文 key 在原始字节流里的真实出现次序，避免只比对内存 dict 而漏掉出口重排。
    app = create_app()
    c = app.test_client()
    c.post('/api/登录', json={'账号': 'admin', '密码': '123456'})
    c.post('/api/模式/声明', json={'来源号': 'co-b-http', '模式': '甲式'})
    # 故意提交乱序正文
    乱序 = '{"优先级":"普通","载荷摘要":"a=1","端点键":"ep-billing","事件类型":"t","事件号":"EVT-H"}'
    r = c.post('/api/模式/组装',
               data=('{"来源号":"co-b-http","正文":' + 乱序 + '}').encode('utf-8'),
               content_type='application/json')
    assert r.status_code == 200
    # 对原始响应文本解析，得到出口实际字段序
    出口序 = list(_json.loads(r.get_data(as_text=True))['正文'].keys())
    assert 出口序 == 甲序, f'出口重排：响应正文实际字段序 {出口序}，应保持甲序 {甲序}'
