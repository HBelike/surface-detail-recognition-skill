"""将真实运行结果转换为上游 skill-creator 的 grading/timing 格式。"""
import argparse
import json
from pathlib import Path
from assertions import grade_summary


def adapt_run(summary_path, labels_path, output_dir):
    summary = json.loads(Path(summary_path).read_text(encoding='utf-8-sig'))
    labels = json.loads(Path(labels_path).read_text(encoding='utf-8-sig'))
    score = grade_summary(summary, labels)
    execution = summary.get('execution')
    if execution is not None:
        usage = execution.get('known_usage', {})
        unknown = execution.get('requests_with_unknown_usage', 0)
        duration = execution.get('elapsed_seconds')
        calls = execution.get('api_calls')
        scope = 'current_execution'
    else:
        usage_list = [r.get('metadata', {}).get('usage') for r in summary.get('records', [])]
        unknown = sum(u is None or 'total_tokens' not in u for u in usage_list)
        usage = {'total_tokens': sum(u.get('total_tokens', 0) for u in usage_list if u)}
        duration = summary.get('wall_seconds')
        if duration is None and summary.get('baseline_wall_seconds') is not None and summary.get('review_wall_seconds') is not None:
            duration = summary['baseline_wall_seconds'] + summary['review_wall_seconds']
        calls = None
        scope = 'recorded_pipeline_totals_cache_not_audited'
    # 不用字符数冒充Token；缺少用量时上游仅展示已知下限，真值保存在timing内。
    timing = {'total_duration_seconds': duration or 0, 'duration_known': duration is not None,
              'total_tokens': usage.get('total_tokens', 0), 'usage_complete': unknown == 0,
              'requests_with_unknown_usage': unknown, 'measurement_scope': scope}
    if summary.get('benchmark_wall_seconds') is not None:
        timing.update(total_duration_seconds=summary['benchmark_wall_seconds'], duration_known=True)
    eligible = [c for c in score['cases'] if c['item_eligible']]
    grading = {'expectations': [{'text': c['item_id'] + ' 两类都与人工标签一致',
                                'passed': c['item_exact'], 'evidence': json.dumps(c['categories'], ensure_ascii=False)} for c in eligible],
               'summary': {'passed': score['item_correct'], 'failed': score['item_total'] - score['item_correct'],
                           'total': score['item_total'], 'pass_rate': score['item_accuracy'] or 0},
               'execution_metrics': {'total_tool_calls': calls or 0,
                                     'errors_encountered': sum(any(v['failed_or_missing'] for v in c['categories'].values()) for c in score['cases'])},
               'user_notes_summary': {'uncertainties': ([] if not unknown else ['Token仅为已知下限，不能据此比较完整费用']) +
                                                       ([] if duration is not None else ['没有完整墙钟耗时，0不是实测耗时']),
                                      'needs_review': ['未验证像素位置；仅有组级人工标签'], 'workarounds': []}}
    output_dir = Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    for name, value in [('grading.json', grading), ('timing.json', timing), ('exact-score.json', score)]:
        (output_dir / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    return score


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary', type=Path, required=True)
    parser.add_argument('--labels', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(adapt_run(args.summary, args.labels, args.output), ensure_ascii=False))
