import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const supabaseAdmin = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!
);

Deno.serve(async (req) => {
  const payload = await req.json();
  const userId = payload?.user_id;

  if (!userId) {
    return new Response(JSON.stringify({ error: "No user_id" }), { status: 400 });
  }

  // Get all company memberships for this user
  const { data: memberships } = await supabaseAdmin
    .from("company_members")
    .select("company_id, role, spend_limit, is_active")
    .eq("user_id", userId)
    .eq("is_active", true)
    .order("created_at", { ascending: false });

  if (!memberships || memberships.length === 0) {
    // New user — no company yet
    return new Response(JSON.stringify({
      company_id: null,
      role: null,
      spend_limit: 0,
      company_count: 0
    }));
  }

  // Use most recently joined active company as default
  const primary = memberships[0];

  return new Response(JSON.stringify({
    company_id: primary.company_id,
    role: primary.role,
    spend_limit: primary.spend_limit,
    company_count: memberships.length,
    // All memberships available for company switcher UI
    all_companies: memberships.map(m => ({
      company_id: m.company_id,
      role: m.role
    }))
  }));
});
