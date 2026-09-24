"""Validate the distributable workflow's graph and input/API wiring offline."""
import plistlib
import re
import tempfile
import unittest
from pathlib import Path

from scripts.build_shortcut import NAME, ROOT, URL_PATTERN, build, workflow
from test_core import USER_SHARE_TEXT, USER_SHORT_URL, USER_WEB_URL


class ShortcutTests(unittest.TestCase):
    def setUp(self):
        self.data = workflow()
        self.actions = self.data["WFWorkflowActions"]

    def test_both_user_inputs_extract_exact_link(self):
        self.assertEqual(re.findall(URL_PATTERN, USER_WEB_URL), [USER_WEB_URL])
        self.assertEqual(re.findall(URL_PATTERN, USER_SHARE_TEXT), [USER_SHORT_URL])
        self.assertFalse(re.findall(URL_PATTERN, "https://douyin.com.evil.test/video/123456789012"))

    def test_every_variable_output_exists_and_control_flow_balances(self):
        seen, groups = set(), []

        def refs(value):
            if isinstance(value, dict):
                if value.get("Type") == "ActionOutput":
                    self.assertIn(value["OutputUUID"], seen)
                for child in value.values():
                    refs(child)
            elif isinstance(value, list):
                for child in value:
                    refs(child)

        for action in self.actions:
            params = action["WFWorkflowActionParameters"]
            refs(params)
            uid = params["UUID"]
            self.assertNotIn(uid, seen)
            seen.add(uid)
            if action["WFWorkflowActionIdentifier"].endswith(".conditional"):
                mode, group = params["WFControlFlowMode"], params["GroupingIdentifier"]
                if mode == 0:
                    groups.append(group)
                    self.assertEqual(params["WFInput"]["Type"], "Variable")
                else:
                    self.assertEqual(groups[-1], group)
                    if mode == 2:
                        groups.pop()
        self.assertEqual(groups, [])

    def test_import_questions_point_to_editable_text_fields(self):
        for question in self.data["WFWorkflowImportQuestions"]:
            params = self.actions[question["ActionIndex"]]["WFWorkflowActionParameters"]
            self.assertEqual(params[question["ParameterKey"]], question["DefaultValue"])

    def test_token_only_sent_to_api_and_download_feeds_photos(self):
        requests = [a["WFWorkflowActionParameters"] for a in self.actions
                    if a["WFWorkflowActionIdentifier"].endswith(".downloadurl")]
        self.assertEqual([r["WFHTTPMethod"] for r in requests], ["POST", "GET"])
        keys = lambda r: [x["WFKey"]["Value"]["string"] for x in
                          r["WFHTTPHeaders"]["Value"]["WFDictionaryFieldValueItems"]]
        self.assertIn("Authorization", keys(requests[0]))
        self.assertNotIn("Authorization", keys(requests[1]))
        self.assertEqual(set(keys(requests[1])), {"Referer", "User-Agent"})
        photos = next(a for a in self.actions if a["WFWorkflowActionIdentifier"].endswith(".savetocameraroll"))
        self.assertEqual(photos["WFWorkflowActionParameters"]["WFInput"]["Value"]["OutputUUID"], requests[1]["UUID"])

    def test_distributed_files_match_reproducible_build(self):
        with tempfile.TemporaryDirectory() as folder:
            build(folder)
            for suffix in (".plist", ".shortcut"):
                data = (Path(folder) / (NAME + suffix)).read_bytes()
                self.assertEqual(data, (ROOT / "src" / (NAME + suffix)).read_bytes())
                self.assertEqual(plistlib.loads(data), self.data)
