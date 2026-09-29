#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""self-drive v2.1 (4) 首回访计量组装件（10-05 周轮回访行数据面·组合既有件零重建）

法源=orders 2026-09-28 Self-Drive v2.0 / P-20260928-02 v2.1④（首计量回访=周报自驱面一行：
三线占比/队列常备/GPU 均值/空转事件/提案数·首回访 10-05 周轮·不达标=修法或升 CEO）。
本件=纯组装器，零新台账零重建：GPU 均值←Tools/gpu_idle_collector.py report（subprocess）；
队列常备←Tools/queue_check.py（subprocess）；三线占比/空转候选←state/rounds.log 轮账本解析；
提案数←state/proposals.md 行解析。输出=回访一行 + JSON 落盘 state/self-drive-metrics-<ts>.json。

口径注记（常驻输出）：
- 三线占比=窗内轮账本 queue 字段首 token 分类计数（P1/main→主业务·P2/tech→技术·P3/explore→新方向；
  T-* 首 token=任务板件计入 board 辅助位·C-* 等其余=other 辅助位·每轮一队列步=1 票）。
- 空转候选=窗内轮账本无队列推进行计数；权威口径仍=审计部哨兵扫+日清声明面，本字段仅供回访对照。
- GPU 均值=采集器全样本均值（DRY-RUN 观察期 2026-09-28 起算=回访窗内全样本即窗均值）。
用法：python Tools/self_drive_metrics.py [--start 2026-09-28] [--end YYYY-MM-DD]
"""
import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_tool(args):
    p = subprocess.run([sys.executable] + args, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', cwd=str(ROOT))
    return (p.stdout or '') + (p.stderr or '')


def gpu_metrics():
    out = _run_tool(['Tools/gpu_idle_collector.py', 'report'])
    m = re.search(r'n=(\d+) avg=([\d.]+)% max=(\d+)% kpi=(\w+)', out)
    if not m:
        return None
    return {'n': int(m.group(1)), 'avg_pct': float(m.group(2)),
            'max_pct': int(m.group(3)), 'kpi': m.group(4)}


def queue_metrics():
    out = _run_tool(['Tools/queue_check.py'])
    per = {}
    for name in ('main', 'tech', 'explore'):
        m = re.search(name + r'\.md: rows=(\d+) open=(\d+) done=(\d+)', out)
        if m:
            per[name] = {'rows': int(m.group(1)), 'open': int(m.group(2)),
                         'done': int(m.group(3))}
    mt = re.search(r'TOTAL open=(\d+)', out)
    return {'per_queue': per, 'total_open': int(mt.group(1)) if mt else None}


def rounds_metrics(start, end):
    # 轮账本 queue 字段首 token 实测分布：P1 M#/tech T#/explore E#（三线）+T-*/C-*（板件/其他）
    r = {'rounds_total': 0, 'line_main': 0, 'line_tech': 0, 'line_explore': 0,
         'line_board': 0, 'line_other': 0, 'idle_candidates': 0}
    path = ROOT / 'state' / 'rounds.log'
    if not path.exists():
        return r
    for raw in path.read_text(encoding='utf-8', errors='replace').splitlines():
        m = re.match(r'(\d{4}-\d{2}-\d{2}) ', raw)
        if not m or not (start <= m.group(1) <= end):
            continue
        r['rounds_total'] += 1
        mq = re.search(r'queue:\s*(\S+)', raw)
        tok = mq.group(1) if mq else ''
        if tok in ('main', 'P1') or re.match(r'^M\d+$', tok):
            r['line_main'] += 1
        elif tok in ('tech', 'P2') or re.match(r'^T\d+$', tok):
            r['line_tech'] += 1
        elif tok in ('explore', 'P3') or re.match(r'^E\d+$', tok):
            r['line_explore'] += 1
        elif re.match(r'^T-\d', tok):
            r['line_board'] += 1
        elif tok in ('', '-', '无'):
            r['idle_candidates'] += 1
        else:
            r['line_other'] += 1
    return r


def proposal_count(start, end):
    n = 0
    path = ROOT / 'state' / 'proposals.md'
    if not path.exists():
        return 0
    for raw in path.read_text(encoding='utf-8', errors='replace').splitlines():
        m = re.match(r'\|\s*BC-P-\d+\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|', raw)
        if m and start <= m.group(1) <= end:
            n += 1
    return n


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='2026-09-28',
                    help='回访窗起（默认=self-drive 三队列建面日 2026-09-28）')
    ap.add_argument('--end', default=datetime.date.today().isoformat())
    a = ap.parse_args()

    g = gpu_metrics()
    q = queue_metrics()
    r = rounds_metrics(a.start, a.end)
    p = proposal_count(a.start, a.end)
    ts = datetime.datetime.now().strftime('%Y%m%d-%H%M')

    pq = q['per_queue']
    qln = r['line_main'] + r['line_tech'] + r['line_explore']

    def _pct(n):
        return '%.0f%%' % (100.0 * n / qln) if qln else 'n/a'

    line = (
        "self-drive metrics {}->{}: "
        "three_line P1(main)={}[{}]/P2(tech)={}[{}]/P3(explore)={}[{}] of queue-line rounds "
        "(rounds_total={} board={} other={}) | "
        "queue_stock rows={}/{}/{} open_total={} | "
        "gpu avg={}% (n={}, kpi={}) | "
        "idle_candidates={} (authoritative=audit-sentinel+daily-report) | "
        "proposals={}"
    ).format(a.start, a.end, r['line_main'], _pct(r['line_main']), r['line_tech'],
             _pct(r['line_tech']), r['line_explore'], _pct(r['line_explore']),
             r['rounds_total'], r['line_board'], r['line_other'],
             pq.get('main', {}).get('rows', '?'), pq.get('tech', {}).get('rows', '?'),
             pq.get('explore', {}).get('rows', '?'), q['total_open'],
             g['avg_pct'] if g else '?', g['n'] if g else '?',
             g['kpi'] if g else '?',
             r['idle_candidates'], p)
    print(line)

    payload = {'window': {'start': a.start, 'end': a.end}, 'gpu': g, 'queue': q,
               'three_line_rounds': r, 'proposals_in_window': p, 'generated': ts,
               'caliber_notes': '三线占比=轮账本 queue 字段计数·空转候选=权威口径对照位·GPU 均值=观察期全样本'}
    out_path = ROOT / 'state' / ('self-drive-metrics-%s.json' % ts)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding='utf-8')
    print('json -> %s' % out_path.relative_to(ROOT))


if __name__ == '__main__':
    main()
