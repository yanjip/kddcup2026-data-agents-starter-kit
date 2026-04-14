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
    
    # 修复方括号不匹配问题
    # 找到应该用 ] 但用了 } 的位置
    if close_brackets < open_brackets and close_braces > open_braces:
        # 这种情况说明有 } 被错误地用来替代了 ]
        # 我们需要找到最左边的那个多余的 }（在数组值后面）
        missing = open_brackets - close_brackets
        extra_braces = close_braces - open_braces
        
        # 从左向右查找，找到第一个在数组上下文中的多余 }
        # 通常这是在 rows 值后面的那个 }
        chars = list(text)
        bracket_depth = 0
        brace_depth = 0
        replaced = 0
        
        for i, char in enumerate(chars):
            if char == '[':
                bracket_depth += 1
            elif char == ']':
                bracket_depth -= 1
            elif char == '{':
                brace_depth += 1
            elif char == '}':
                # 如果在方括号深度 > 0 的情况下遇到 }，这可能是错误
                if bracket_depth > 0 and replaced < missing and replaced < extra_braces:
                    chars[i] = ']'
                    bracket_depth -= 1
                    replaced += 1
                else:
                    brace_depth -= 1
        
        text = ''.join(chars)
    elif close_brackets < open_brackets:
        # 单纯缺少闭合方括号，在末尾补充
        text += ']' * (open_brackets - close_brackets)
    
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
