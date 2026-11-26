"""晚卷 · test_schema_co_c.py —— 争用失败测：进出两向改同一条的模式声明，字段序互相覆盖，式红。

骨签：旧模式字段序不得被新模式重排。
本卷只验“并发与一致性”：
  1. 两路并发对同一来源双向改模式声明（甲→新、新→甲），不得互相覆盖出半旧半新的撕裂态；
  2. 声明落定后只有一个确定模式，且该来源已组装投递的字段序与落定模式严格一致；
  3. 模式版本 + 载荷详情 + 投递列表三处口径一致，能看清每条用的是哪一序。
"""
import os
import tempfile
import threading
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ['数据目录'] = _tmp
os.environ['夹具目录'] = str(Path(__file__).resolve().parents[2] / 'fixtures')

from app import seed
from app.services import 夹具, 模式组装

甲序 = ['事件号', '事件类型', '端点键', '载荷摘要', '优先级']
新序 = ['事件号', '优先级', '载荷摘要', '事件类型', '端点键']
规范序 = {'甲式': 甲序, '新式': 新序}


def setup_module():
    seed.初始化库()


def _甲式正文() -> dict:
    return 夹具.读取('模式复现/甲式正文')['事件'][0]


def test_两路并发改同一条声明不互相覆盖():
    来源 = 'co-c-1'
    模式组装.声明模式(来源, '甲式')
    组装号 = 模式组装.组装(来源, _甲式正文())['投递号']

    屏障 = threading.Barrier(2)
    错误 = []

    def 改(目标: str):
        try:
            屏障.wait()
            for _ in range(50):
                模式组装.声明模式(来源, 目标)
        except Exception as e:  # noqa: BLE001
            错误.append(repr(e))

    t1 = threading.Thread(target=改, args=('新式',))
    t2 = threading.Thread(target=改, args=('甲式',))
    t1.start(); t2.start()
    t1.join(); t2.join()
    assert not 错误, f'并发改声明抛错：{错误}'

    落定 = 模式组装.取声明(来源)
    assert 落定['模式'] in ('甲式', '新式')
    # 声明只有一个确定值，不得出现半旧半新：版本号、模式、字段序三者自洽
    assert 落定['字段顺序'] == 规范序[落定['模式']], (
        f"式红：声明模式 {落定['模式']} 与字段顺序 {落定['字段顺序']} 互相覆盖撕裂"
    )

    # 已组装投递是声明收成时的不可变快照，不被并发声明回刷
    旧详情 = 模式组装.投递详情(组装号)
    assert 旧详情['模式'] == '甲式'
    assert 旧详情['字段顺序'] == 甲序

    # 模式版本 + 载荷详情 + 投递列表三处口径一致
    列 = {r['投递号']: r for r in 模式组装.读集合(None)}
    行 = 列[组装号]
    assert 行['字段顺序'] == 旧详情['字段顺序'] == 甲序
    assert 行['模式版本'] == 旧详情['模式版本']


def test_并发组装各依其声明快照不串序():
    # 两个来源各自声明不同模式，并发组装，互不串序
    模式组装.声明模式('co-c-甲', '甲式')
    模式组装.声明模式('co-c-新', '新式')
    屏障 = threading.Barrier(2)
    结果 = {}

    def 装(来源: str, 序):
        屏障.wait()
        正文 = 模式组装.按序(序, _甲式正文())
        结果[来源] = 模式组装.组装(来源, 正文)

    ta = threading.Thread(target=装, args=('co-c-甲', 甲序))
    tb = threading.Thread(target=装, args=('co-c-新', 新序))
    ta.start(); tb.start()
    ta.join(); tb.join()

    甲详情 = 模式组装.投递详情(结果['co-c-甲']['投递号'])
    新详情 = 模式组装.投递详情(结果['co-c-新']['投递号'])
    assert list(甲详情['正文'].keys()) == 甲序
    assert list(新详情['正文'].keys()) == 新序
    assert 甲详情['字段顺序'] == 甲序 and 新详情['字段顺序'] == 新序
