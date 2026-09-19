-- US classification override layer
-- Re-runnable seed/migration for the Fundamental-app project.
--
-- Policy:
--   Sector         = service/UI sector
--   Analysis group = business-model routing
--   Scoring model  = scoring_profile
--   Standard       = default scoring model for ordinary operating companies
--   Financial      = generic financial analysis group when no narrower type
--   SPAC           = separate classification, excluded from fundamental eligibility
--
-- Reviewed: 2026-09

create table if not exists public."US_Company_Classification_Overrides" (
  ticker text primary key,
  sector_common text,
  sector_common_ko text,
  company_type text,
  scoring_profile text,
  is_fundamental_eligible boolean,
  reason text not null,
  source_url text,
  reviewed_at timestamptz not null default now(),
  active boolean not null default true
);

create index if not exists us_company_classification_overrides_active_idx
on public."US_Company_Classification_Overrides" (ticker)
where active = true;

create or replace function public.apply_us_company_classification_override()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  o public."US_Company_Classification_Overrides"%rowtype;
begin
  select *
    into o
    from public."US_Company_Classification_Overrides"
   where ticker = new.ticker
     and active = true;

  if found then
    if o.sector_common is not null then new.sector_common := o.sector_common; end if;
    if o.sector_common_ko is not null then new.sector_common_ko := o.sector_common_ko; end if;
    if o.company_type is not null then new.company_type := o.company_type; end if;
    if o.scoring_profile is not null then new.scoring_profile := o.scoring_profile; end if;
    if o.is_fundamental_eligible is not null then new.is_fundamental_eligible := o.is_fundamental_eligible; end if;
    new.sector_source := 'MANUAL_OVERRIDE';
  end if;

  return new;
end;
$$;

revoke all on function public.apply_us_company_classification_override() from public;

drop trigger if exists trg_apply_us_company_classification_override
on public."US_Companies";

create trigger trg_apply_us_company_classification_override
before insert or update of sector_source, sector_common, sector_common_ko, company_type, scoring_profile, is_fundamental_eligible
on public."US_Companies"
for each row
execute function public.apply_us_company_classification_override();

alter table public."US_Company_Classification_Overrides" enable row level security;

-- Generic Financial overrides: 105 companies.
insert into public."US_Company_Classification_Overrides"
  (ticker, sector_common, sector_common_ko, company_type, scoring_profile,
   is_fundamental_eligible, reason, reviewed_at, active)
values
('EEFT','financials','금융','financial','financial',NULL),
('LSAK','financials','금융','financial','financial',NULL),
('USIO','financials','금융','financial','financial',NULL),
('AGM','financials','금융','financial','financial',NULL),
('FMCC','financials','금융','financial','financial',NULL),
('FNMA','financials','금융','financial','financial',NULL),
('AFRM','financials','금융','financial','financial',NULL),
('ATLCP','financials','금융','financial','financial',NULL),
('BFH','financials','금융','financial','financial',NULL),
('CACC','financials','금융','financial','financial',NULL),
('ENVA','financials','금융','financial','financial',NULL),
('HAPN','financials','금융','financial','financial',NULL),
('NNI','financials','금융','financial','financial',NULL),
('OMF','financials','금융','financial','financial',NULL),
('PICS','financials','금융','financial','financial',NULL),
('RM','financials','금융','financial','financial',NULL),
('SLM','financials','금융','financial','financial',NULL),
('WRLD','financials','금융','financial','financial',NULL),
('ECPG','financials','금융','financial','financial',NULL),
('JCAP','financials','금융','financial','financial',NULL),
('OMCC','financials','금융','financial','financial',NULL),
('PLBC','financials','금융','financial','financial',NULL),
('PRAA','financials','금융','financial','financial',NULL),
('SWRD','financials','금융','financial','financial',NULL),
('IX','financials','금융','financial','financial',NULL),
('NRUC','financials','금융','financial','financial',NULL),
('SPFX','financials','금융','financial','financial',NULL),
('SWKHL','financials','금융','financial','financial',NULL),
('TROO','financials','금융','financial','financial',NULL),
('BLNE','financials','금융','financial','financial',NULL),
('FOA','financials','금융','financial','financial',NULL),
('ONIT','financials','금융','financial','financial',NULL),
('PFSI','financials','금융','financial','financial',NULL),
('RKT','financials','금융','financial','financial',NULL),
('UWMC','financials','금융','financial','financial',NULL),
('BETR','financials','금융','financial','financial',NULL),
('FIGR','financials','금융','financial','financial',NULL),
('FINV','financials','금융','financial','financial',NULL),
('TAAG','financials','금융','financial','financial',NULL),
('TREE','financials','금융','financial','financial',NULL),
('JBK','financials','금융','financial','financial',NULL),
('KTH','financials','금융','financial','financial',NULL),
('KTN','financials','금융','financial','financial',NULL),
('AMTD','financials','금융','financial','financial',NULL),
('ATCH','financials','금융','financial','financial',NULL),
('AXP','financials','금융','financial','financial',NULL),
('BCCG','financials','금융','financial','financial',NULL),
('BCG','financials','금융','financial','financial',NULL),
('BENF','financials','금융','financial','financial',NULL),
('BKKT','financials','금융','financial','financial',NULL),
('BLSH','financials','금융','financial','financial',NULL),
('BMHL','financials','금융','financial','financial',NULL),
('BRBI','financials','금융','financial','financial',NULL),
('BTGO','financials','금융','financial','financial',NULL),
('BUR','financials','금융','financial','financial',NULL),
('CHYM','financials','금융','financial','financial',NULL),
('CNCK','financials','금융','financial','financial',NULL),
('CNF','financials','금융','financial','financial',NULL),
('COIN','financials','금융','financial','financial',NULL),
('CPSS','financials','금융','financial','financial',NULL),
('CRCL','financials','금융','financial','financial',NULL),
('DAVE','financials','금융','financial','financial',NULL),
('EXOD','financials','금융','financial','financial',NULL),
('FGCO','financials','금융','financial','financial',NULL),
('FGNX','financials','금융','financial','financial',NULL),
('FLD','financials','금융','financial','financial',NULL),
('FRMM','financials','금융','financial','financial',NULL),
('GDOT','financials','금융','financial','financial',NULL),
('GEMI','financials','금융','financial','financial',NULL),
('GHI','financials','금융','financial','financial',NULL),
('HKD','financials','금융','financial','financial',NULL),
('JFIN','financials','금융','financial','financial',NULL),
('JFU','financials','금융','financial','financial',NULL),
('KLAR','financials','금융','financial','financial',NULL),
('LDI','financials','금융','financial','financial',NULL),
('LU','financials','금융','financial','financial',NULL),
('LX','financials','금융','financial','financial',NULL),
('MATH','financials','금융','financial','financial',NULL),
('MDBH','financials','금융','financial','financial',NULL),
('MEGL','financials','금융','financial','financial',NULL),
('MFIN','financials','금융','financial','financial',NULL),
('MGLD','financials','금융','financial','financial',NULL),
('MGTE','financials','금융','financial','financial',NULL),
('NCPL','financials','금융','financial','financial',NULL),
('NU','financials','금융','financial','financial',NULL),
('OPFI','financials','금융','financial','financial',NULL),
('OPRT','financials','금융','financial','financial',NULL),
('PAPL','financials','금융','financial','financial',NULL),
('PGY','financials','금융','financial','financial',NULL),
('PWP','financials','금융','financial','financial',NULL),
('QFIN','financials','금융','financial','financial',NULL),
('RHLD','financials','금융','financial','financial',NULL),
('SECZ','financials','금융','financial','financial',NULL),
('SHFS','financials','금융','financial','financial',NULL),
('SII','financials','금융','financial','financial',NULL),
('SNFCA','financials','금융','financial','financial',NULL),
('SNTG','financials','금융','financial','financial',NULL),
('SOFI','financials','금융','financial','financial',NULL),
('SYF','financials','금융','financial','financial',NULL),
('UPST','financials','금융','financial','financial',NULL),
('VEL','financials','금융','financial','financial',NULL),
('WD','financials','금융','financial','financial',NULL),
('WLTH','financials','금융','financial','financial',NULL),
('XYF','financials','금융','financial','financial',NULL),
('YRD','financials','금융','financial','financial',NULL)
on conflict (ticker) do update set
  sector_common=excluded.sector_common,
  sector_common_ko=excluded.sector_common_ko,
  company_type=excluded.company_type,
  scoring_profile=excluded.scoring_profile,
  is_fundamental_eligible=excluded.is_fundamental_eligible,
  reason=excluded.reason,
  reviewed_at=excluded.reviewed_at,
  active=true;

-- SPAC / blank-check entities: SEC SIC 6770.
insert into public."US_Company_Classification_Overrides"
  (ticker, sector_common, sector_common_ko, company_type, scoring_profile,
   is_fundamental_eligible, reason, reviewed_at, active)
select ticker, 'other', '기타', 'spac', 'standard', false,
       'SPAC / blank-check entity (SEC SIC 6770); excluded from fundamental scoring universe.',
       now(), true
from public."US_Companies"
where company_type = 'spac'
on conflict (ticker) do update set
  sector_common=excluded.sector_common,
  sector_common_ko=excluded.sector_common_ko,
  company_type=excluded.company_type,
  scoring_profile=excluded.scoring_profile,
  is_fundamental_eligible=excluded.is_fundamental_eligible,
  reason=excluded.reason,
  reviewed_at=excluded.reviewed_at,
  active=true;

-- Preserve previously validated special classifications not fully represented
-- by the older classifier implementation.
insert into public."US_Company_Classification_Overrides"
  (ticker, sector_common, sector_common_ko, company_type, scoring_profile,
   is_fundamental_eligible, reason, reviewed_at, active)
select ticker, sector_common, sector_common_ko, company_type, scoring_profile,
       is_fundamental_eligible,
       'Preserve previously validated special classification.',
       now(), true
from public."US_Companies"
where company_type in ('defense','mining_royalty','midstream')
on conflict (ticker) do update set
  sector_common=excluded.sector_common,
  sector_common_ko=excluded.sector_common_ko,
  company_type=excluded.company_type,
  scoring_profile=excluded.scoring_profile,
  is_fundamental_eligible=excluded.is_fundamental_eligible,
  reason=excluded.reason,
  reviewed_at=excluded.reviewed_at,
  active=true;

-- Apply all active overrides once.
update public."US_Companies" u
set sector_source = 'MANUAL_OVERRIDE',
    sector_common = o.sector_common,
    sector_common_ko = o.sector_common_ko,
    company_type = o.company_type,
    scoring_profile = o.scoring_profile,
    is_fundamental_eligible = coalesce(o.is_fundamental_eligible, u.is_fundamental_eligible)
from public."US_Company_Classification_Overrides" o
where o.ticker = u.ticker
  and o.active = true;
