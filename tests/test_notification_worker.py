import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class NotificationWorkerContractTests(unittest.TestCase):
    def test_worker_exposes_authenticated_notification_evaluation(self):
        source = (PROJECT_ROOT / "data-proxy" / "src" / "index.js").read_text(
            encoding="utf-8"
        )
        runtime = (PROJECT_ROOT / "data-proxy" / "src" / "strategy-runtime.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('url.pathname === "/notification-evaluations"', source)
        self.assertIn("requireNotificationAuth(request, env)", source)
        self.assertIn("historyOptionsForTicker(ticker, hasCompositeValuation)", source)
        self.assertIn('const COMPOSITE_HISTORY_OPTIONS = {lookbackDays: 2200, tailRows: 1500}', source)
        self.assertIn("addCompositeValuationScore(data)", source)
        self.assertIn("runStrategyIncremental(definitions, definition, data", source)
        self.assertIn("selectNotificationAlerts(result.history", source)
        self.assertIn("weekly_summary", source)
        self.assertIn("scheduled_summary", source)
        self.assertIn("summary_schedule", source)
        self.assertIn("const preview = body.preview === true", source)
        self.assertIn("if (!preview) await saveNotificationSnapshot", source)
        self.assertIn("notification_context", runtime)
        self.assertIn("mapped_products:true", runtime)
        self.assertIn("privateDefinitions(body, publicDefinitions)", source)
        self.assertIn("private_strategies", source)
        self.assertIn('url.pathname === "/notification-bootstrap"', source)
        self.assertIn("function expressionTokens", runtime)
        self.assertNotIn("return Function", runtime)

    def test_apps_script_uses_worker_for_evaluation_and_keeps_secrets_out_of_sheet(self):
        source = (PROJECT_ROOT / "docs" / "google-apps-script" / "Code.gs").read_text(
            encoding="utf-8"
        )

        self.assertIn("function runNotificationCheck()", source)
        self.assertIn("function requestEvaluations_(subscriptions, preview)", source)
        self.assertIn("PropertiesService.getScriptProperties()", source)
        self.assertIn("LockService.getScriptLock()", source)
        self.assertIn("function eventAlreadySent_(key)", source)
        self.assertIn("SHEETS.PERSONAL", source)
        self.assertIn("function privateStrategies_(subscriptions)", source)
        self.assertIn("function initializeNotificationStates()", source)
        self.assertIn("function isWeeklySummaryDay_(date)", source)
        self.assertIn("function scheduledSummaryDue_(summary)", source)
        self.assertIn("function isMonthlySummaryDay_(date)", source)
        self.assertIn("function sendScheduledSummaryTest()", source)
        self.assertIn("requestEvaluations_(subscriptions, true)", source)
        self.assertIn("function requestPreviewWithSeed_(subscriptions)", source)
        self.assertIn("if (!message.includes('notification seed is required:')) throw error", source)
        self.assertIn("function signalBar_(value, max)", source)
        self.assertIn("function formatAllocationTable_(event)", source)
        self.assertIn("event.product_names?.[ticker] || ticker", source)
        self.assertIn("'현재 → 목표'", source)
        self.assertNotIn("parse_mode: 'HTML'", source)
        self.assertIn("'📌 시장 상태'", source)
        self.assertIn("'📊 신호'", source)
        self.assertIn("'📈 시장 지표'", source)
        self.assertIn("'[사전주의 · 매매 없음]'", source)
        self.assertIn("'[주간 시장 브리핑]'", source)
        self.assertIn("formatWeightChanges_(event)", source)


if __name__ == "__main__":
    unittest.main()
