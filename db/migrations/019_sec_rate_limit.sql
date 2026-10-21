-- One SEC rate limit shared by every process on hermes (the SEC allows 10 requests/second per
-- IP across all our traffic). libs/sec (tributary_sec.shared) calls ops.sec_take_token before
-- every SEC request and ops.sec_report_throttle when the SEC answers 429 or "Request Rate
-- Threshold Exceeded". The Python twin `MemoryBucket` in libs/sec mirrors this algorithm for
-- unit tests; change both together.
--
-- Token bucket: `rate` tokens/second refill up to `capacity`. Any one-second window sees at most
-- capacity + rate requests (2 + 7 = 9 by default), sustained 7/s. Times come from the database
-- clock, so all pods agree.
--
-- Priorities: a caller class may only take a token when, after taking it, `reserve` tokens are
-- left. live (poller, doc-fetcher for new filings) has reserve 0, so it can always use the
-- tokens a batch or bulk caller has to leave behind and is never queued behind them; when live
-- drains the bucket below a class's reserve, that class waits until it refills. Lower classes
-- reach their threshold later, so live > batch > bulk, and bulk still gets the whole rate when
-- nobody else is asking.
--
-- Cooldown: a throttle report empties the bucket and stops refills until cooldown_until, for
-- every class. Cooldowns double (10 s, 20 s, ... capped at 600 s, the SEC's own block length)
-- while throttles keep coming within 30 minutes of each other. Reports during a cooldown come
-- from requests already in flight and are ignored.
--
-- Tuning: UPDATE ops.sec_rate_limit / ops.sec_rate_classes; callers pick it up on their next
-- request. Unknown classes get the largest reserve.

CREATE TABLE ops.sec_rate_limit (
    bucket           text PRIMARY KEY,
    rate             double precision NOT NULL CHECK (rate > 0),
    capacity         double precision NOT NULL CHECK (capacity >= 1),
    tokens           double precision NOT NULL,
    -- tokens is the level at updated_at; during a cooldown updated_at is in the future
    updated_at       timestamptz NOT NULL DEFAULT clock_timestamp(),
    cooldown_until   timestamptz NOT NULL DEFAULT '-infinity',
    strikes          integer NOT NULL DEFAULT 0,
    last_throttle_at timestamptz,
    granted          bigint NOT NULL DEFAULT 0,
    throttles        bigint NOT NULL DEFAULT 0
);

CREATE TABLE ops.sec_rate_classes (
    class   text PRIMARY KEY,
    reserve double precision NOT NULL CHECK (reserve >= 0)
);

INSERT INTO ops.sec_rate_limit (bucket, rate, capacity, tokens) VALUES ('sec', 7, 2, 2);
INSERT INTO ops.sec_rate_classes (class, reserve) VALUES
    ('live', 0),
    ('batch', 0.5),
    ('bulk', 1);

-- Take one token for p_class. wait_s = 0: granted, send the request. wait_s > 0: nothing was
-- taken; try again after about wait_s seconds. Also returns the bucket level and the global
-- granted count for metrics.
CREATE FUNCTION ops.sec_take_token(p_class text, p_bucket text DEFAULT 'sec')
RETURNS TABLE (wait_s double precision, tokens double precision, granted bigint,
               cooling boolean)
LANGUAGE plpgsql AS $$
DECLARE
    b       ops.sec_rate_limit%ROWTYPE;
    now_    timestamptz := clock_timestamp();
    v_reserve double precision;
    v_level   double precision;
    v_need    double precision;
BEGIN
    SELECT * INTO b FROM ops.sec_rate_limit r WHERE r.bucket = p_bucket FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unknown SEC rate-limit bucket %', p_bucket;
    END IF;

    SELECT c.reserve INTO v_reserve FROM ops.sec_rate_classes c WHERE c.class = p_class;
    IF NOT FOUND THEN
        SELECT coalesce(max(c.reserve), 0) INTO v_reserve FROM ops.sec_rate_classes c;
    END IF;
    v_reserve := least(v_reserve, b.capacity - 1);
    v_need := 1 + v_reserve;

    IF now_ < b.updated_at THEN  -- cooling down: no refill until updated_at
        RETURN QUERY SELECT extract(epoch FROM b.updated_at - now_)::float8
                                + greatest(0, v_need - b.tokens) / b.rate,
                            b.tokens, b.granted, true;
        RETURN;
    END IF;

    v_level := least(b.capacity,
                     b.tokens + extract(epoch FROM now_ - b.updated_at)::float8 * b.rate);
    IF v_level >= v_need - 1e-9 THEN
        UPDATE ops.sec_rate_limit r
        SET tokens = v_level - 1, updated_at = now_, granted = r.granted + 1
        WHERE r.bucket = p_bucket;
        RETURN QUERY SELECT 0::float8, v_level - 1, b.granted + 1, false;
    ELSE
        RETURN QUERY SELECT (v_need - v_level) / b.rate, v_level, b.granted, false;
    END IF;
END
$$;

-- The SEC said we're too fast: start (or keep) a shared cooldown. Returns its remaining seconds.
CREATE FUNCTION ops.sec_report_throttle(p_bucket text DEFAULT 'sec')
RETURNS double precision
LANGUAGE plpgsql AS $$
DECLARE
    b       ops.sec_rate_limit%ROWTYPE;
    now_    timestamptz := clock_timestamp();
    v_strikes integer;
    v_secs    double precision;
BEGIN
    SELECT * INTO b FROM ops.sec_rate_limit r WHERE r.bucket = p_bucket FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unknown SEC rate-limit bucket %', p_bucket;
    END IF;
    IF now_ < b.cooldown_until THEN
        RETURN extract(epoch FROM b.cooldown_until - now_)::float8;
    END IF;

    v_strikes := CASE WHEN b.last_throttle_at > now_ - interval '30 minutes'
                    THEN b.strikes + 1 ELSE 0 END;
    v_secs := least(600, 10 * power(2, least(v_strikes, 6)));
    UPDATE ops.sec_rate_limit r
    SET tokens = 0,
        updated_at = now_ + make_interval(secs => v_secs),
        cooldown_until = now_ + make_interval(secs => v_secs),
        strikes = v_strikes,
        last_throttle_at = now_,
        throttles = r.throttles + 1
    WHERE r.bucket = p_bucket;
    RETURN v_secs;
END
$$;

-- For the pipeline-health dashboard (FR11): the bucket now, without taking a token.
CREATE VIEW ops.sec_rate_limit_status AS
SELECT bucket,
       rate,
       capacity,
       CASE WHEN clock_timestamp() < updated_at THEN tokens
            ELSE least(capacity, tokens
                       + extract(epoch FROM clock_timestamp() - updated_at)::float8 * rate)
       END AS tokens,
       clock_timestamp() < cooldown_until AS cooling,
       nullif(cooldown_until, '-infinity') AS cooldown_until,
       strikes,
       last_throttle_at,
       granted,
       throttles
FROM ops.sec_rate_limit;

GRANT SELECT ON ops.sec_rate_limit_status TO ops_reader;
