"""人工明确标签的确定性断言；拒判、缺失与流程失败保留在分母。"""
CATEGORIES = ('linear_trace', 'material_loss')
STATES = ('present', 'not_observed', 'uncertain')


def grade_case(prediction, human_label):
    eligible = [c for c in CATEGORIES if human_label.get(c) in STATES[:2]]
    failed = prediction is None
    outcomes = {}
    for category in eligible:
        predicted = None if failed else prediction.get(category)
        if predicted is not None and predicted not in STATES:
            raise ValueError('模型结果包含非法类别状态')
        actual = human_label[category]
        outcomes[category] = {
            'truth': actual, 'predicted': predicted, 'correct': predicted == actual,
            'false_positive': actual == 'not_observed' and predicted == 'present',
            'false_negative': actual == 'present' and predicted != 'present',
            'uncertain': predicted == 'uncertain', 'failed_or_missing': predicted is None,
        }
    return {'item_id': human_label.get('item_id'), 'categories': outcomes,
            'category_correct': sum(v['correct'] for v in outcomes.values()),
            'category_total': len(eligible), 'item_eligible': len(eligible) == 2,
            'item_exact': len(eligible) == 2 and all(v['correct'] for v in outcomes.values())}


def grade_summary(summary, labels):
    if labels.get('source') != 'human': raise ValueError('仅接受人工标签 source=human')
    truth = labels.get('labels', [])
    records = summary.get('records', [])
    for rows in (truth, records):
        ids = [row['item_id'] for row in rows]
        if len(ids) != len(set(ids)): raise ValueError('物体ID重复')
    lookup = {row['item_id']: row for row in records}
    grades = []
    for label in truth:
        if label.get('reviewed') is not True: continue
        record = lookup.get(label['item_id'])
        completed = record and record.get('status') == 'completed' and record.get('refinement', {}).get('status') != 'failed'
        grades.append(grade_case(record.get('result') if completed else None, label))
    correct = sum(g['category_correct'] for g in grades)
    total = sum(g['category_total'] for g in grades)
    joint = [g for g in grades if g['item_eligible']]
    passed = sum(g['item_exact'] for g in joint)
    return {'category_correct': correct, 'category_total': total,
            'category_accuracy': correct / total if total else None,
            'item_correct': passed, 'item_total': len(joint),
            'item_accuracy': passed / len(joint) if joint else None, 'cases': grades,
            'localization': {'status': 'not_evaluated', 'reason': '组级标签不含人工位置框'}}
