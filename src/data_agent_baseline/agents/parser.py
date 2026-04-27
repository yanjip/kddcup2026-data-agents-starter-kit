from __future__ import annotations

import json
import re

from data_agent_baseline.agents.model import ModelStep


def _strip_json_fence(raw_response: str) -> str:
    text = raw_response.strip()
    fence_match = re.search(r"```json\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if fence_match is not None:
        return fence_match.group(1).strip()
    generic_fence_match = re.search(r"```\s*(.*?)\s*```", text, flags=re.DOTALL)
    if generic_fence_match is not None:
        return generic_fence_match.group(1).strip()
    return text


def _sanitize_json_string(text: str) -> str:
    """清理 JSON 字符串中的非法控制字符。
    
    LLM 有时会在 JSON 字符串值中包含未转义的换行符等控制字符，
    这会导致 json.loads 解析失败。此函数将这些字符替换为转义形式。
    
    Args:
        text: 原始 JSON 字符串
        
    Returns:
        清理后的 JSON 字符串
    """
    # 替换未转义的换行符为转义形式
    # 注意：我们需要处理 JSON 字符串值中的实际换行符
    # 但保留 JSON 结构中的有效换行（如果有的话）
    
    result = []
    in_string = False
    escape_next = False
    
    for char in text:
        if escape_next:
            if char in '"\\/bfnrtu':
                result.append(char)
            else:
                # 非法 JSON 转义序列（如 \s, \d, \w 等），将反斜杠双写
                # 这样 JSON 解析后得到原始的 \s, \d 等，保持原意
                result.append('\\')
                result.append(char)
            escape_next = False
            continue
            
        if char == '\\':
            result.append(char)
            escape_next = True
            continue
            
        if char == '"':
            in_string = not in_string
            result.append(char)
            continue
            
        if in_string and ord(char) < 32:
            # 在字符串值中，将控制字符转为转义形式
            if char == '\n':
                result.append('\\n')
            elif char == '\r':
                result.append('\\r')
            elif char == '\t':
                result.append('\\t')
            else:
                # 其他控制字符用 unicode 转义
                result.append(f'\\u{ord(char):04x}')
        else:
            result.append(char)
    
    return ''.join(result)


def _fix_common_json_errors(text: str) -> str:
    """修复常见的 JSON 格式错误。
    
    LLM 有时会在生成 answer 工具的 action_input 时出现括号不匹配的问题，
    例如：rows:[["a","b",1]]} 写成 rows:[["a","b",1}]}
    
    Args:
        text: 原始 JSON 字符串
        
    Returns:
        修复后的 JSON 字符串
    """
    # 统计括号数量
    open_braces = text.count('{')
    close_braces = text.count('}')
    open_brackets = text.count('[')
    close_brackets = text.count(']')
    
    missing_brackets = open_brackets - close_brackets
    extra_braces = close_braces - open_braces
    
    # 修复方括号不匹配问题
    if missing_brackets > 0:
        best_candidate = None
        # 从右向左尝试在 } 或 ] 前面插入 missing 个 ]，找到第一个能解析成功的
        for insert_pos in range(len(text) - 1, -1, -1):
            if text[insert_pos] in '}]':
                candidate = text[:insert_pos] + ']' * missing_brackets + text[insert_pos:]
                try:
                    json.loads(candidate)
                    best_candidate = candidate
                    break
                except json.JSONDecodeError:
                    continue
        if best_candidate:
            text = best_candidate
        else:
            # 如果简单插入不行，且同时有多余的 }，尝试将 } 替换为 ]
            if extra_braces > 0:
                replacement_count = min(missing_brackets, extra_braces)
                for replace_pos in range(len(text) - 1, -1, -1):
                    if text[replace_pos] == '}':
                        candidate = text[:replace_pos] + ']' + text[replace_pos + 1:]
                        try:
                            json.loads(candidate)
                            text = candidate
                            missing_brackets -= 1
                            extra_braces -= 1
                            replacement_count -= 1
                            if replacement_count <= 0:
                                break
                        except json.JSONDecodeError:
                            continue
            # 如果替换也失败，fallback 到末尾追加
            if missing_brackets > 0:
                text += ']' * missing_brackets
    
    # 重新统计，修复花括号不匹配问题
    open_braces = text.count('{')
    close_braces = text.count('}')
    
    if close_braces < open_braces:
        # 缺少闭合花括号，在末尾补充
        text += '}' * (open_braces - close_braces)
    elif close_braces > open_braces:
        # 多余的闭合花括号，从末尾移除
        extra = close_braces - open_braces
        for _ in range(extra):
            if text.endswith('}'):
                text = text[:-1]
    
    # 移除末尾的多余字符（如多余的双引号）
    # LLM 有时会在 JSON 对象后添加多余的 " 或其他字符
    text = _strip_trailing_garbage(text)
    
    return text


def _strip_trailing_garbage(text: str) -> str:
    """移除 JSON 对象后的多余字符。
    
    LLM 有时会在 JSON 对象闭合后添加多余的字符，如双引号、逗号等。
    此函数找到第一个完整的 JSON 对象并返回。
    
    Args:
        text: 原始 JSON 字符串
        
    Returns:
        清理后的 JSON 字符串
    """
    # 尝试找到第一个完整的 JSON 对象
    brace_count = 0
    in_string = False
    escape_next = False
    
    for i, char in enumerate(text):
        if escape_next:
            escape_next = False
            continue
            
        if char == '\\':
            escape_next = True
            continue
            
        if char == '"' and not escape_next:
            in_string = not in_string
            continue
            
        if not in_string:
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    # 找到完整的 JSON 对象，返回到这里为止
                    return text[:i + 1]
    
    return text


def _load_single_json_object(text: str) -> dict[str, object]:
    payload, end = json.JSONDecoder().raw_decode(text)
    remainder = text[end:].strip()
    if remainder:
        cleaned_remainder = re.sub(r"(?:\\[nrt])+", "", remainder).strip()
        if cleaned_remainder:
            raise ValueError("Model response must contain only one JSON object.")
    if not isinstance(payload, dict):
        raise ValueError("Model response must be a JSON object.")
    return payload


def parse_model_step(raw_response: str) -> ModelStep:
    normalized = _strip_json_fence(raw_response)
    sanitized = _sanitize_json_string(normalized)
    fixed = _fix_common_json_errors(sanitized)
    payload = _load_single_json_object(fixed)

    thought = payload.get("thought", "")
    action = payload.get("action")
    action_input = payload.get("action_input", {})
    if not isinstance(thought, str):
        raise ValueError("thought must be a string.")
    if action is None:
        raise ValueError("Model response must contain an 'action' key.")
    if not isinstance(action, str) or not action.strip():
        raise ValueError("action must be a non-empty string.")
    if not isinstance(action_input, dict):
        raise ValueError("action_input must be a JSON object.")

    return ModelStep(
        thought=thought,
        action=action,
        action_input=action_input,
        raw_response=raw_response,
    )
