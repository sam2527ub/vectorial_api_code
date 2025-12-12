-- Add optional audienceRoomId to ScrapeJob for auto-mapping posts
ALTER TABLE "ScrapeJob" ADD COLUMN IF NOT EXISTS "audienceRoomId" TEXT;

-- Index for faster lookups by audience room
CREATE INDEX IF NOT EXISTS "ScrapeJob_audienceRoomId_idx" ON "ScrapeJob"("audienceRoomId");
