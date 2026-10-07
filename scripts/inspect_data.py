import json

for name, path in [('benchmark', 'tests/fixtures/full-scenario-benchmark-v1.4.json'), ('scenarios', 'configs/scenarios-v1.4.json')]:
    try:
        data = json.load(open(path, encoding='utf-8'))
        print(f'==== {name} ====')
        print('类型:', type(data).__name__)
        if isinstance(data, dict):
            keys = list(data.keys())
            print('顶层键:', keys[:10])
            for k in keys[:3]:
                v = data[k]
                print(f'  {k}: {type(v).__name__}, len={len(v) if hasattr(v, "__len__") else "?"}')
                if isinstance(v, list) and v:
                    print('  样例[0]:', json.dumps(v[0], ensure_ascii=False)[:400])
                elif isinstance(v, dict):
                    print('  子键:', list(v.keys())[:8])
        elif isinstance(data, list):
            print('条数:', len(data))
            print('样例[0]:', json.dumps(data[0], ensure_ascii=False)[:400])
    except Exception as e:
        print(f'==== {name} 读取失败: {e} ====')
