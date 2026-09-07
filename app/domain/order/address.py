# -*- coding: utf-8 -*-
"""
Address 收货地址（Value Object）

===========================================
什么是值对象？
===========================================
- 没有唯一标识，只靠值来判断是否相等
- 不可变（创建后不能修改）
- 两个 Address 如果所有字段相同，就是"同一个地址"

===========================================
为什么 country 很重要？
===========================================
跨境购物中，收货国家决定了：
1. **关税**：不同国家税率不同（美国数码配件 0%，中国 13%）
2. **运费**：距离越远运费越高
3. **可达性**：有些商品不能寄到某些国家

所以 `country` 是关税/运费计算的关键字段。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)  # 不可变
class Address:
    """
    收货地址值对象

    属性:
        recipient_name: 收件人姓名
        country: 国家代码（如 "CN"、"US"）
        state: 州/省（如 "California"、"浙江"）
        city: 城市
        address_line: 详细地址
        postal_code: 邮政编码
        phone: 联系电话

    不变量:
        - recipient_name 不能为空
        - country 不能为空
        - city 不能为空
        - address_line 不能为空
    """

    recipient_name: str    # 收件人姓名
    country: str           # 国家代码
    state: str             # 州/省
    city: str              # 城市
    address_line: str      # 详细地址
    postal_code: str       # 邮政编码
    phone: str             # 联系电话

    def __post_init__(self) -> None:
        """
        创建后的验证（不变量检查）

        确保关键字段不为空

        异常:
            ValueError: 关键字段为空
        """
        for field_name in ("recipient_name", "country", "city", "address_line"):
            if not getattr(self, field_name):
                raise ValueError(f"Address.{field_name} required")

    def one_line(self) -> str:
        """
        生成单行地址字符串

        返回:
            格式化的地址字符串

        示例:
            >>> address.one_line()
            'CN 浙江 杭州市 西湖区xx路1号（张三 13800000000）'
        """
        return f"{self.country} {self.state} {self.city} {self.address_line}（{self.recipient_name} {self.phone}）"
