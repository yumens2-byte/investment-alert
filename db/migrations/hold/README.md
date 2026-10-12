# db/migrations/hold — 적용 보류 마이그레이션

`006_enable_rls_alert_tables.sql`(alert 계열 8개 테이블 RLS ON + anon/authenticated 권한 회수)은 **적용을 보류**한다. 운영 `SUPABASE_KEY`의 role이 확정되지 않았기 때문이다. 마스터 진술은 "service_role 아님, 변경 없음"이지만, 운영 DB `pg_stat_statements` 기준으로 해당 테이블을 실행한 role은 전부 `service_role`이어서 둘이 충돌한다. 실제 키가 anon 키인 상태에서 006을 적용하면 AlertStore의 모든 조회·저장이 실패한다. 그러면 쿨다운 조회가 False로 무력화돼 X 중복 발행 위험이 생기고, claim과 ia_alert_run 기록도 실패한다. 키 role을 확인한 뒤(예: JWT payload의 `role` 클레임) `db/migrations/`로 옮겨 적용한다.
