# -*- coding: utf-8 -*-
"""
test_ai.py - AI 模型测试脚本
用于测试 Microsoft Florence-2-base 模型的加载
"""


import os
os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
import sys
from pathlib import Path

# 确保 Python 3.10+
if sys.version_info < (3, 10):
    print(f"错误: 需要 Python 3.10+, 当前版本 {sys.version_info.major}.{sys.version_info.minor}")
    sys.exit(1)

try:
    from transformers import AutoModelForCausalLM, AutoProcessor
except ImportError:
    print("错误: 未安装 transformers 库，请先运行: pip install transformers")
    sys.exit(1)


def load_florence2_model():
    """
    加载 Microsoft Florence-2-base 模型。
    第一次运行会自动从 Hugging Face 下载模型。
    """
    model_id = "microsoft/Florence-2-base"
    
    print(f"正在加载模型: {model_id}")
    print("首次运行会自动下载模型，请耐心等待...")
    
    # 加载处理器（Tokenizer + Image Processor）
    processor = AutoProcessor.from_pretrained(
        model_id,
        trust_remote_code=True
    )
    
    # 加载模型
    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        trust_remote_code=True
    )
    
    return model, processor


def main():
    print("=" * 50)
    print("AI Photo Manager - 模型加载测试")
    print("=" * 50)
    print()
    
    try:
        model, processor = load_florence2_model()
        print()
        print("模型加载成功")
        print()
        print(f"模型类型: {type(model).__name__}")
        print(f"处理器类型: {type(processor).__name__}")
        
    except Exception as e:
        print(f"\n模型加载失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()