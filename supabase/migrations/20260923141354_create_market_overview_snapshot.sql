create table if not exists public.market_overview_snapshot (
  market text not null,
  symbol text not null,
  label text not null,
  metric_group text not null,
  value double precision,
  change_pct double precision,
  change_abs double precision,
  asof_date date,
  source text not null,
  updated_at timestamptz not null default now(),
  primary key (market, symbol)
);

create index if not exists market_overview_snapshot_market_group_idx
  on public.market_overview_snapshot (market, metric_group, symbol);

alter table public.market_overview_snapshot enable row level security;

revoke all on table public.market_overview_snapshot from anon, authenticated;
grant select on table public.market_overview_snapshot to anon, authenticated;

drop policy if exists "Public can read market overview snapshots"
  on public.market_overview_snapshot;

create policy "Public can read market overview snapshots"
  on public.market_overview_snapshot
  for select
  to anon, authenticated
  using (true);
