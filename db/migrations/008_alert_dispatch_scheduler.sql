-- ============================================================================
-- Migration: 008_alert_dispatch_scheduler
-- Date:      2026-10-13
-- Author:    GTT팀 (alert.yml 외부 스케줄러 — B안)
-- Purpose:   GitHub schedule 실행률 저하(실측 ~10%) 대응.
--            Supabase pg_cron 이 창 시각마다 GitHub API 로 alert.yml 을
--            workflow_dispatch(source=scheduler) 실행시키고, 결과를 기록·경보한다.
--
-- 전제:
--   1) alert.yml 에 workflow_dispatch input `source`(manual|scheduler) 반영 완료
--      (미반영 상태에서 호출하면 GitHub 가 422 Unexpected inputs 로 거부 → 로그에 기록됨)
--   2) Vault 시크릿 (Supabase Dashboard > Project Settings > Vault 에서 마스터가 직접 등록)
--        github_alert_dispatch_pat     : fine-grained PAT (repo=yumens2-byte/investment-alert, Actions: Read and write)
--        ia_tg_bot_token               : (선택) 내부 경보용 텔레그램 봇 토큰
--        ia_tg_internal_chat_id        : (선택) 내부 경보 채널 ID
--      시크릿이 없으면 dispatch 는 'no_secret' 으로 기록만 하고 GitHub 호출을 하지 않는다.
--
-- 스케줄 (cron.timezone = GMT, 2026-10-13 확인):
--   ia_alert_dispatch            '21,33 13-15,19-20 * * 1-5'  (평일 10회, EDT/EST 양쪽 ET 09:30/10:30/15:30 창 커버)
--   ia_alert_dispatch_reconcile  '*/10 * * * *'                (응답 정산 + 실패 경보)
--
-- 멱등:      IF NOT EXISTS / CREATE OR REPLACE / cron.schedule(이름) 은 같은 이름이면 갱신
-- 롤백:      파일 하단 ROLLBACK 참조
-- ============================================================================

-- 1. 확장
create extension if not exists pg_cron;
create extension if not exists pg_net with schema extensions;

-- 2. 비노출 스키마 (PostgREST 노출 대상 아님)
create schema if not exists private;
revoke all on schema private from public, anon, authenticated;

-- 3. 디스패치 로그
create table if not exists private.ia_dispatch_log (
    id               bigserial    primary key,
    requested_at     timestamptz  not null default now(),
    dry_run          boolean      not null default false,
    status           text         not null,          -- requested | no_secret | error | ok | failed | lost
    request_id       bigint,                         -- net.http_post 반환 id
    http_status      integer,
    workflow_run_id  bigint,
    response_excerpt text,                           -- 응답 본문 앞 300자 (토큰 미포함)
    error            text,
    reconciled_at    timestamptz,
    notified_at      timestamptz,
    constraint chk_ia_dispatch_log_status check (status in
        ('requested','no_secret','error','ok','failed','lost'))
);
create index if not exists idx_ia_dispatch_log_requested on private.ia_dispatch_log (requested_at desc);
create index if not exists idx_ia_dispatch_log_open
    on private.ia_dispatch_log (request_id) where reconciled_at is null;
revoke all on private.ia_dispatch_log from public, anon, authenticated;

-- 4. 디스패치 함수
create or replace function private.ia_dispatch_alert(p_dry_run boolean default false)
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_pat text;
    v_req bigint;
begin
    select ds.decrypted_secret into v_pat
      from vault.decrypted_secrets ds
     where ds.name = 'github_alert_dispatch_pat'
     limit 1;

    if v_pat is null or length(trim(v_pat)) = 0 then
        insert into private.ia_dispatch_log (dry_run, status, error)
        values (p_dry_run, 'no_secret', 'vault secret github_alert_dispatch_pat 없음');
        return null;
    end if;

    v_req := net.http_post(
        url := 'https://api.github.com/repos/yumens2-byte/investment-alert/actions/workflows/alert.yml/dispatches',
        body := jsonb_build_object(
            'ref', 'main',
            'inputs', jsonb_build_object(
                'dry_run', case when p_dry_run then 'true' else 'false' end,
                'source', 'scheduler')),
        headers := jsonb_build_object(
            'Authorization', 'Bearer ' || trim(v_pat),
            'Accept', 'application/vnd.github+json',
            'X-GitHub-Api-Version', '2022-11-28',
            'User-Agent', 'investment-alert-scheduler',
            'Content-Type', 'application/json'),
        timeout_milliseconds := 10000);

    insert into private.ia_dispatch_log (dry_run, status, request_id)
    values (p_dry_run, 'requested', v_req);
    return v_req;
exception when others then
    insert into private.ia_dispatch_log (dry_run, status, error)
    values (p_dry_run, 'error', left(sqlerrm, 300));
    return null;
end;
$$;
revoke all on function private.ia_dispatch_alert(boolean) from public, anon, authenticated;

-- 5. 정산 + 실패 경보 함수
create or replace function private.ia_dispatch_reconcile()
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_bot  text;
    v_chat text;
    v_fail integer;
    v_msg  text;
begin
    -- (a) 응답 정산: 2xx = ok, 그 외/timeout/오류 = failed
    update private.ia_dispatch_log l
       set http_status      = r.status_code,
           response_excerpt = left(coalesce(r.content, r.error_msg, ''), 300),
           workflow_run_id  = case
                                when r.status_code between 200 and 299
                                 and r.content is not null
                                 and r.content ~ '"workflow_run_id"\s*:\s*[0-9]+'
                                then (substring(r.content from '"workflow_run_id"\s*:\s*([0-9]+)'))::bigint
                              end,
           status           = case
                                when r.status_code between 200 and 299 and not coalesce(r.timed_out, false)
                                then 'ok' else 'failed'
                              end,
           error            = case when coalesce(r.timed_out, false) then 'timed_out'
                                   else left(r.error_msg, 300) end,
           reconciled_at    = now()
      from net._http_response r
     where r.id = l.request_id
       and l.reconciled_at is null
       and l.status = 'requested';

    -- (b) 응답이 6시간 TTL 로 사라진 미정산 건 = lost
    update private.ia_dispatch_log
       set status = 'lost', reconciled_at = now()
     where status = 'requested'
       and reconciled_at is null
       and requested_at < now() - interval '6 hours';

    -- (c) 미통지 실패 건 경보 (TG 시크릿이 있을 때만, 1회 묶음 발송)
    select count(*) into v_fail
      from private.ia_dispatch_log
     where status in ('failed','lost','error','no_secret')
       and notified_at is null;

    if v_fail = 0 then
        return 0;
    end if;

    select ds.decrypted_secret into v_bot  from vault.decrypted_secrets ds where ds.name = 'ia_tg_bot_token' limit 1;
    select ds.decrypted_secret into v_chat from vault.decrypted_secrets ds where ds.name = 'ia_tg_internal_chat_id' limit 1;

    if v_bot is null or v_chat is null then
        return v_fail;   -- 경보 수단 없음: 로그에만 남김 (notified_at 은 비워 둠)
    end if;

    select '[alert-dispatch] 실패 ' || count(*) || '건: '
           || string_agg(status || coalesce('(' || http_status || ')', '')
                         || ' ' || to_char(requested_at at time zone 'UTC', 'MM-DD HH24:MI') || 'Z',
                         ', ' order by requested_at)
      into v_msg
      from private.ia_dispatch_log
     where status in ('failed','lost','error','no_secret')
       and notified_at is null;

    perform net.http_post(
        url := 'https://api.telegram.org/bot' || trim(v_bot) || '/sendMessage',
        body := jsonb_build_object('chat_id', trim(v_chat), 'text', left(v_msg, 3500)),
        timeout_milliseconds := 10000);

    update private.ia_dispatch_log
       set notified_at = now()
     where status in ('failed','lost','error','no_secret')
       and notified_at is null;

    return v_fail;
end;
$$;
revoke all on function private.ia_dispatch_reconcile() from public, anon, authenticated;

-- 6. pg_net 요청/응답 테이블 노출 차단 (요청 큐에 Authorization 헤더가 잠시 저장됨)
revoke all on all tables in schema net from anon, authenticated;

-- 7. 스케줄 등록 (같은 이름이면 갱신)
select cron.schedule('ia_alert_dispatch',
                     '21,33 13-15,19-20 * * 1-5',
                     $$select private.ia_dispatch_alert(false)$$);
select cron.schedule('ia_alert_dispatch_reconcile',
                     '*/10 * * * *',
                     $$select private.ia_dispatch_reconcile()$$);

-- ============================================================================
-- 검증
--   select jobname, schedule, active from cron.job where jobname like 'ia_alert_dispatch%';
--   select private.ia_dispatch_alert(true);   -- DRY_RUN 디스패치 1회 (GitHub 에 dispatch 실행 생성 확인)
--   select private.ia_dispatch_reconcile();   -- 10초 후 실행
--   select * from private.ia_dispatch_log order by id desc limit 5;   -- status=ok, http_status=200, workflow_run_id
--   select * from cron.job_run_details where jobid in
--     (select jobid from cron.job where jobname like 'ia_alert_dispatch%') order by start_time desc limit 10;
-- ============================================================================
-- ROLLBACK
--   select cron.unschedule('ia_alert_dispatch');
--   select cron.unschedule('ia_alert_dispatch_reconcile');
--   drop function if exists private.ia_dispatch_reconcile();
--   drop function if exists private.ia_dispatch_alert(boolean);
--   drop table if exists private.ia_dispatch_log;
--   (확장 pg_cron / pg_net 은 다른 용도 사용 여부 확인 후 별도 판단)
-- ============================================================================
