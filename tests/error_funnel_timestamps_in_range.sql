-- severity: error
-- protects: the PM table never shows a future or pre-launch date (bad rows are quarantined upstream)
select user_key, signed_up_at, email_verified_at, id_verified_at, first_deposit_at, first_trade_at
from fct_onboarding_funnel
where signed_up_at not between {{ launch_at }} and {{ as_of }}
   or email_verified_at not between {{ launch_at }} and {{ as_of }}
   or id_verified_at not between {{ launch_at }} and {{ as_of }}
   or first_deposit_at not between {{ launch_at }} and {{ as_of }}
   or first_trade_at not between {{ launch_at }} and {{ as_of }}
