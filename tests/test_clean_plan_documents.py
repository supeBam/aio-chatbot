import unittest

from clean_plan_documents import clean_document


class CleanPlanDocumentsTests(unittest.TestCase):
    def test_removes_only_requested_fields_in_matching_sections(self):
        source = """# 套餐文档 - TEST
### 基础信息
项目编码: TEST
语言: cn
服务类型: AS
套餐参考月费: 998
合约月数: 24
### 优惠政策
- 111111
- 0
- 12.5
- Wi-Fi 7 路由器規格
- 最短合約期內可免每月$28行政費
### 价格信息条目 1
- plan_code: TEST
- 合约后月费: $1198 /月
- 违约金: 合約期的剩餘月費
- 998
- 0.5
- installationFee:豁免(基本安裝費：$680)
- 优惠月费: 第1至24個月月費: $998
- 合约期: 24個月合約
### 价格信息条目 2
- plan_code：ADDON
- 0
### 功能信息条目 1
- plan_code: KEEP
- 语言: KEEP
- 111111
- 每日全速1GB旅遊數據
"""
        result, removed = clean_document(source)
        self.assertEqual(removed, 11)
        for line in (
            "语言: cn\n", "- 12.5\n", "- plan_code: TEST\n", "- plan_code：ADDON\n",
            "- 合约后月费: $1198 /月\n", "- 违约金: 合約期的剩餘月費\n", "- 998\n",
        ):
            self.assertNotIn(line, result)
        for line in (
            "项目编码: TEST", "服务类型: AS", "套餐参考月费: 998", "合约月数: 24",
            "- Wi-Fi 7 路由器規格", "- 最短合約期內可免每月$28行政費",
            "- installationFee:豁免(基本安裝費：$680)",
            "- 优惠月费: 第1至24個月月費: $998", "- 合约期: 24個月合約",
            "- plan_code: KEEP", "- 语言: KEEP", "- 111111", "- 每日全速1GB旅遊數據",
        ):
            self.assertIn(line, result)
        self.assertEqual(clean_document(result), (result, 0))

    def test_preserves_line_endings_and_untouched_text(self):
        source = "### 基础信息\r\n语言：cn\r\n方案名称: 5G\r\n\r\n### 功能信息条目 1\r\n- 24\r\n"
        result, removed = clean_document(source)
        self.assertEqual(result, source.replace("语言：cn\r\n", ""))
        self.assertEqual(removed, 1)

    def test_other_sections_are_unchanged(self):
        source = "# Other\n语言: cn\n- plan_code: KEEP\n- 111111\n### 功能信息条目 1\n- 0"
        self.assertEqual(clean_document(source), (source, 0))


if __name__ == "__main__":
    unittest.main()
