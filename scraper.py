#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""兼容入口：等同于 `python -m proppulse.run`。参数相同，例如：
  python scraper.py                      # 全部阶段
  python scraper.py --offline            # 不联网重算
  python scraper.py --discover           # 诊断
  python scraper.py --stages news,social,forecast
"""
from proppulse.run import main

if __name__ == "__main__":
    main()
