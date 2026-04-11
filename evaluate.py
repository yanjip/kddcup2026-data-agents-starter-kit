#!/usr/bin/env python3
"""
评分脚本：对比预测结果与标准答案，计算 KDD Cup 2026 DataAgent-Bench 的评分

评分规则：
- Recall = Matched Columns / Gold Columns
- Score = Recall - λ · (Extra Columns / Predicted Columns)
- 最终分数下限为 0
"""

import os
import json
import csv
import argparse
from pathlib import Path
from typing import Optional
from dataclasses import dataclass


@dataclass
class TaskScore:
    """单个任务的评分结果"""
    task_id: str
    gold_columns: int
    predicted_columns: int
    matched_columns: int
    extra_columns: int
    recall: float
    penalty: float
    score: float
    succeeded: bool
    failure_reason: Optional[str] = None


def load_csv_columns(filepath: str) -> set:
    """加载 CSV 文件的列名（表头）"""
    if not os.path.exists(filepath):
        return set()
    
    with open(filepath, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        try:
            headers = next(reader)
            return set(headers)
        except StopIteration:
            return set()


def compute_column_signatures(filepath: str) -> dict:
    """
    计算 CSV 文件的列签名（官方规则版本）
    
    列签名 = 该列所有值的排序后的元组（忽略行顺序）
    返回: {列名: 列签名} 和 列签名计数（支持重复列）
    """
    if not os.path.exists(filepath):
        return {}, {}
    
    with open(filepath, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        
        # 收集每列的所有值
        column_values = {col: [] for col in headers}
        for row in reader:
            for col in headers:
                column_values[col].append(row.get(col, ''))
        
        # 计算列签名：排序后的值元组
        signatures = {}
        signature_counts = {}
        for col in headers:
            # 排序所有值，转换为元组作为签名
            sorted_values = tuple(sorted(column_values[col]))
            signatures[col] = sorted_values
            signature_counts[sorted_values] = signature_counts.get(sorted_values, 0) + 1
        
        return signatures, signature_counts


def match_columns_by_content(
    gold_sigs: dict, 
    gold_counts: dict,
    pred_sigs: dict,
    pred_counts: dict
) -> tuple:
    """
    基于列内容签名匹配 gold 和 prediction 的列（官方规则版本）
    
    匹配逻辑：
    - 忽略列名，只比较列签名（排序后的值）
    - 支持重复列（相同签名需匹配相同次数）
    - 按签名出现次数进行匹配
    
    返回: (matched_columns, extra_columns)
    - matched_columns: 成功匹配的列数
    - extra_columns: prediction 中未匹配的列数
    """
    # 计算每个签名可以匹配的次数
    matched = 0
    extra = 0
    
    # 统计 prediction 中每个签名需要匹配的次数
    pred_signature_list = list(pred_sigs.values())
    gold_signature_list = list(gold_sigs.values())
    
    # 使用贪心匹配：对于每个唯一的签名，计算匹配次数
    all_signatures = set(pred_signature_list) | set(gold_signature_list)
    
    for sig in all_signatures:
        gold_count = gold_counts.get(sig, 0)
        pred_count = pred_counts.get(sig, 0)
        
        # 匹配次数 = min(gold中出现次数, pred中出现次数)
        match_count = min(gold_count, pred_count)
        matched += match_count
        
        # 多余的列
        extra += max(0, pred_count - gold_count)
    
    return matched, extra


def calculate_task_score(
    gold_path: str,
    prediction_path: str,
    lambda_penalty: float = 0.1
) -> Optional[TaskScore]:
    """
    计算单个任务的评分（官方规则版本）
    
    Args:
        gold_path: 标准答案 CSV 路径
        prediction_path: 预测结果 CSV 路径
        lambda_penalty: 惩罚项权重 λ
    
    Returns:
        TaskScore 对象，如果无法计算则返回 None
    """
    # 加载列签名和计数
    gold_sigs, gold_counts = compute_column_signatures(gold_path)
    pred_sigs, pred_counts = compute_column_signatures(prediction_path)
    
    if not gold_sigs:
        return None
    
    gold_columns = len(gold_sigs)
    predicted_columns = len(pred_sigs)
    
    if predicted_columns == 0:
        # 没有预测结果
        return TaskScore(
            task_id="",
            gold_columns=gold_columns,
            predicted_columns=0,
            matched_columns=0,
            extra_columns=0,
            recall=0.0,
            penalty=0.0,
            score=0.0,
            succeeded=False,
            failure_reason="No prediction file"
        )
    
    # 基于内容签名匹配列
    matched_columns, extra_columns = match_columns_by_content(
        gold_sigs, gold_counts, pred_sigs, pred_counts
    )
    
    # 计算 Recall
    recall = matched_columns / gold_columns if gold_columns > 0 else 0.0
    
    # 计算惩罚项
    penalty = lambda_penalty * (extra_columns / predicted_columns) if predicted_columns > 0 else 0.0
    
    # 计算最终分数
    score = max(0.0, recall - penalty)
    
    return TaskScore(
        task_id="",
        gold_columns=gold_columns,
        predicted_columns=predicted_columns,
        matched_columns=matched_columns,
        extra_columns=extra_columns,
        recall=recall,
        penalty=penalty,
        score=score,
        succeeded=True
    )


def evaluate_run(
    gold_dir: str,
    prediction_dir: str,
    summary_path: str,
    lambda_penalty: float = 0.1
) -> dict:
    """
    评估整个运行结果
    
    Args:
        gold_dir: 标准答案目录（如 public/output）
        prediction_dir: 预测结果目录（如 artifacts/runs/example_run_id）
        summary_path: summary.json 路径
        lambda_penalty: 惩罚项权重 λ
    
    Returns:
        包含所有任务评分的字典
    """
    # 加载 summary.json
    with open(summary_path, 'r', encoding='utf-8') as f:
        summary = json.load(f)
    
    results = []
    total_score = 0.0
    scored_tasks = 0
    
    for task_info in summary.get('tasks', []):
        task_id = task_info['task_id']
        succeeded = task_info['succeeded']
        failure_reason = task_info.get('failure_reason')
        
        gold_path = os.path.join(gold_dir, task_id, 'gold.csv')
        prediction_path = task_info.get('prediction_csv_path')
        
        if not succeeded or not prediction_path:
            # 任务失败或没有预测结果
            # 尝试获取 gold 列数
            gold_sigs, _ = compute_column_signatures(gold_path)
            gold_columns = len(gold_sigs)
            
            task_score = TaskScore(
                task_id=task_id,
                gold_columns=gold_columns,
                predicted_columns=0,
                matched_columns=0,
                extra_columns=0,
                recall=0.0,
                penalty=0.0,
                score=0.0,
                succeeded=False,
                failure_reason=failure_reason or "No prediction"
            )
        else:
            task_score = calculate_task_score(gold_path, prediction_path, lambda_penalty)
            if task_score is None:
                continue
            task_score.task_id = task_id
        
        results.append(task_score)
        total_score += task_score.score
        scored_tasks += 1
    
    average_score = total_score / scored_tasks if scored_tasks > 0 else 0.0
    
    return {
        'run_id': summary.get('run_id', 'unknown'),
        'task_count': summary.get('task_count', 0),
        'succeeded_task_count': summary.get('succeeded_task_count', 0),
        'scored_tasks': scored_tasks,
        'total_score': total_score,
        'average_score': average_score,
        'lambda_penalty': lambda_penalty,
        'task_scores': results
    }


def print_results(results: dict):
    """打印评分结果"""
    print("=" * 80)
    print(f"评分结果 - Run ID: {results['run_id']}")
    print("=" * 80)
    print(f"\n总体统计:")
    print(f"  - 总任务数: {results['task_count']}")
    print(f"  - 成功任务数: {results['succeeded_task_count']}")
    print(f"  - 已评分任务数: {results['scored_tasks']}")
    print(f"  - 惩罚项权重 λ: {results['lambda_penalty']}")
    print(f"  - 总分: {results['total_score']:.4f}")
    print(f"  - 平均分: {results['average_score']:.4f}")
    
    print(f"\n{'=' * 80}")
    print("各任务详细评分:")
    print(f"{'=' * 80}")
    print(f"{'Task ID':<15} {'Gold':>6} {'Pred':>6} {'Match':>6} {'Extra':>6} {'Recall':>8} {'Penalty':>8} {'Score':>8} {'Status':<20}")
    print("-" * 100)
    
    for task in results['task_scores']:
        status = "✓" if task.succeeded else f"✗ ({task.failure_reason[:15]}...)" if task.failure_reason else "✗"
        print(f"{task.task_id:<15} {task.gold_columns:>6} {task.predicted_columns:>6} "
              f"{task.matched_columns:>6} {task.extra_columns:>6} "
              f"{task.recall:>8.4f} {task.penalty:>8.4f} {task.score:>8.4f} {status:<20}")
    
    print("=" * 100)


def save_results(results: dict, output_path: str):
    """保存评分结果到 JSON 文件"""
    # 将 TaskScore 对象转换为字典
    results_dict = {
        'run_id': results['run_id'],
        'task_count': results['task_count'],
        'succeeded_task_count': results['succeeded_task_count'],
        'scored_tasks': results['scored_tasks'],
        'total_score': results['total_score'],
        'average_score': results['average_score'],
        'lambda_penalty': results['lambda_penalty'],
        'task_scores': [
            {
                'task_id': t.task_id,
                'gold_columns': t.gold_columns,
                'predicted_columns': t.predicted_columns,
                'matched_columns': t.matched_columns,
                'extra_columns': t.extra_columns,
                'recall': t.recall,
                'penalty': t.penalty,
                'score': t.score,
                'succeeded': t.succeeded,
                'failure_reason': t.failure_reason
            }
            for t in results['task_scores']
        ]
    }
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results_dict, f, indent=2, ensure_ascii=False)
    
    print(f"\n评分结果已保存到: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description='KDD Cup 2026 DataAgent-Bench 评分脚本',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python evaluate.py --gold-dir public/output --prediction-dir artifacts/runs/example_run_id
  python evaluate.py --gold-dir public/output --prediction-dir artifacts/runs/example_run_id --lambda 0.2
  uv run python evaluate.py --gold-dir demo_samples/output --prediction-dir artifacts/runs/aliyun_run_id_14
        """
    )
    
    parser.add_argument(
        '--gold-dir',
        type=str,
        default='public/output',
        help='标准答案目录路径 (默认: public/output)'
    )
    
    parser.add_argument(
        '--prediction-dir',
        type=str,
        default='artifacts/runs/example_run_id',
        help='预测结果目录路径 (默认: artifacts/runs/example_run_id)'
    )
    
    parser.add_argument(
        '--lambda',
        type=float,
        dest='lambda_penalty',
        default=0.1,
        help='惩罚项权重 λ (默认: 0.1)'
    )
    
    parser.add_argument(
        '--output',
        type=str,
        default=None,
        help='输出结果保存路径 (JSON 格式)'
    )
    
    args = parser.parse_args()
    
    # 检查路径
    gold_dir = Path(args.gold_dir)
    prediction_dir = Path(args.prediction_dir)
    summary_path = prediction_dir / 'summary.json'
    
    if not gold_dir.exists():
        print(f"错误: 标准答案目录不存在: {gold_dir}")
        return 1
    
    if not prediction_dir.exists():
        print(f"错误: 预测结果目录不存在: {prediction_dir}")
        return 1
    
    if not summary_path.exists():
        print(f"错误: summary.json 不存在: {summary_path}")
        return 1
    
    # 执行评分
    results = evaluate_run(
        str(gold_dir),
        str(prediction_dir),
        str(summary_path),
        args.lambda_penalty
    )
    
    # 打印结果
    print_results(results)
    
    # 保存结果
    if args.output:
        save_results(results, args.output)
    
    return 0


if __name__ == '__main__':
    exit(main())