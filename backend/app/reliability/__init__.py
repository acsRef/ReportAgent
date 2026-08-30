"""ReportAgent reliability 包（P9，伞形 plan §七 Reliability Layer）。

出错怎么办：timeout / retry / backoff / errors 四模块，独立横切层，
不建 runtime/ 聚合层。统一 Failure Pipeline：
Error → Classify → Record Trace → Determine Recoverability → Retry/Resume/Fail
→ Persist State → User-visible Error。
"""
