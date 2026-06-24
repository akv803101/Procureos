-- supabase/storage.sql
-- GST invoice storage bucket. Apply AFTER migrations (needs current_company_id()
-- helper from migration 014). Schema source: supabase.md Step 4 — verbatim.
-- Upload path convention: gst-invoices/{company_id}/{order_id}/{invoice}.pdf

INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
  'gst-invoices',
  'gst-invoices',
  false,           -- NEVER public
  10485760,        -- 10MB limit
  ARRAY['application/pdf', 'image/jpeg', 'image/png']
);

-- Storage RLS: a company can only access its own invoices (first path segment).
CREATE POLICY "invoices_own_company" ON storage.objects
  FOR ALL USING (
    bucket_id = 'gst-invoices' AND
    (storage.foldername(name))[1] = current_company_id()::text
  );
