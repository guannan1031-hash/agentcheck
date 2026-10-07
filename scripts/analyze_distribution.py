import json
from collections import Counter

bench = json.load(open('tests/fixtures/full-scenario-benchmark-v1.4.json', encoding='utf-8'))
scen = json.load(open('configs/scenarios-v1.4.json', encoding='utf-8'))

cases = bench['cases']
scenarios = scen['scenarios']

print('== benchmark cases 路由分布 ==')
print(Counter(c['expected_route'] for c in cases))
print('\n== benchmark cases 场景域分布(取前10) ==')
# 用 scene_id 关联 scenarios 的 domain
sc_map = {s['id']: s for s in scenarios}
print(Counter(sc_map.get(c['expected_scene_id'], {}).get('domain', '未知') for c in cases))
print('\n== scenarios 风险级别分布 ==')
print(Counter(s.get('risk_level', '未知') for s in scenarios))
print('\n== scenarios 域分布 ==')
print(Counter(s.get('domain', '未知') for s in scenarios))
print('\n== 样例文本(3条) ==')
for c in cases[:3]:
    print('-', c['synthetic_text'])
print('\n== 样例场景定义(1条) ==')
print(json.dumps(scenarios[0], ensure_ascii=False, indent=1)[:600])
