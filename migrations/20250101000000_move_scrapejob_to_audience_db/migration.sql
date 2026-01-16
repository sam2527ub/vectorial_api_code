-- Migration: Move ScrapeJob table to audience database
-- This migration safely creates the ScrapeJob table in the audience database
-- ONLY touches the ScrapeJob table - no other tables are modified

-- Create ScrapeJob table if it doesn't exist
CREATE TABLE IF NOT EXISTS "ScrapeJob" (
    "id" TEXT NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'PENDING',
    "linkedinUrls" JSONB NOT NULL,
    "maxPosts" INTEGER NOT NULL,
    "apifyRunId" TEXT,
    "result" JSONB,
    "error" TEXT,
    "audienceRoomId" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "ScrapeJob_pkey" PRIMARY KEY ("id")
);

-- Add audienceRoomId column if table exists but column doesn't
DO $$ 
BEGIN 
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name='ScrapeJob' AND column_name='audienceRoomId'
    ) THEN
        ALTER TABLE "ScrapeJob" ADD COLUMN "audienceRoomId" TEXT;
    END IF;
END $$;

-- Create indexes if they don't exist
CREATE INDEX IF NOT EXISTS "ScrapeJob_status_idx" ON "ScrapeJob"("status");
CREATE INDEX IF NOT EXISTS "ScrapeJob_createdAt_idx" ON "ScrapeJob"("createdAt");
CREATE INDEX IF NOT EXISTS "ScrapeJob_audienceRoomId_idx" ON "ScrapeJob"("audienceRoomId");
