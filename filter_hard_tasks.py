#!/usr/bin/env python3
"""
筛选回答错误的 task，复制到 hard_input 文件夹
Score 为 1.0000 才算正确，其他都是错误的
"""

import os
import re
import shutil


def parse_score_file(score_file_path):
    """解析评分结果文件，返回错误的 task ID 列表"""
    wrong_tasks = []
    
    with open(score_file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            # 匹配 task 行，格式如: task_11              3      3      3      0   1.0000   0.0000   1.0000 ✓
            match = re.match(r'(task_\d+)\s+\d+\s+\d+\s+\d+\s+\d+\s+[\d.]+\s+[\d.]+\s+([\d.]+)', line)
            if match:
                task_id = match.group(1)
                score = float(match.group(2))
                # Score 不为 1.0000 视为错误
                if score < 1.0:
                    wrong_tasks.append(task_id)
    
    return wrong_tasks


def copy_task_folders(wrong_tasks, input_dir, output_dir):
    """复制错误的 task 文件夹到输出目录"""
    copied_count = 0
    
    for task_id in wrong_tasks:
        src_path = os.path.join(input_dir, task_id)
        dst_path = os.path.join(output_dir, task_id)
        
        if os.path.exists(src_path):
            try:
                shutil.copytree(src_path, dst_path)
                print(f"已复制: {task_id}")
                copied_count += 1
            except Exception as e:
                print(f"复制 {task_id} 失败: {e}")
        else:
            print(f"警告: 未找到 {task_id} 文件夹")
    
    return copied_count


def main():
    # 配置路径
    score_file = "/Users/yanjp/PycharmProjects/kddcup2026-data-agents-starter-kit/score/aaliyun_run_id_9_results.txt"
    input_dir = "/Users/yanjp/PycharmProjects/kddcup2026-data-agents-starter-kit/demo_samples/input"
    output_dir = "/Users/yanjp/PycharmProjects/kddcup2026-data-agents-starter-kit/demo_samples/hard_input"
    
    print("=" * 60)
    print("筛选回答错误的 Task")
    print("=" * 60)
    
    # 1. 解析评分文件，获取错误的 task
    print(f"\n正在解析评分文件: {score_file}")
    wrong_tasks = parse_score_file(score_file)
    print(f"发现 {len(wrong_tasks)} 个回答错误的 task:")
    for task_id in wrong_tasks:
        print(f"  - {task_id}")
    
    # 2. 创建输出目录
    if os.path.exists(output_dir):
        print(f"\n输出目录已存在，删除旧目录: {output_dir}")
        shutil.rmtree(output_dir)
    
    os.makedirs(output_dir)
    print(f"创建输出目录: {output_dir}")
    
    # 3. 复制错误的 task 文件夹
    print(f"\n开始复制 task 文件夹...")
    copied_count = copy_task_folders(wrong_tasks, input_dir, output_dir)
    
    # 4. 输出统计
    print("\n" + "=" * 60)
    print("处理完成!")
    print(f"  - 错误 task 总数: {len(wrong_tasks)}")
    print(f"  - 成功复制: {copied_count}")
    print(f"  - 输出目录: {output_dir}")
    print("=" * 60)


if __name__ == "__main__":
    main()
