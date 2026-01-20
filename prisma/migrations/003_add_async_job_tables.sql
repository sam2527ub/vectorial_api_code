-- Migration: Add async job tracking tables for profile summaries, classifier, and parallel scraping

-- Async Profile Summaries Job tracking
CREATE TABLE IF NOT EXISTS "SummariesJob" (
    "id" TEXT NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'PENDING',
    "audienceRoomId" TEXT NOT NULL,
    "totalProfiles" INTEGER NOT NULL DEFAULT 0,
    "processedProfiles" INTEGER NOT NULL DEFAULT 0,
    "successCount" INTEGER NOT NULL DEFAULT 0,
    "skippedCount" INTEGER NOT NULL DEFAULT 0,
    "errorCount" INTEGER NOT NULL DEFAULT 0,
    "currentChunk" INTEGER NOT NULL DEFAULT 0,
    "totalChunks" INTEGER NOT NULL DEFAULT 0,
    "error" TEXT,
    "taskToken" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "SummariesJob_pkey" PRIMARY KEY ("id")
);

CREATE INDEX IF NOT EXISTS "SummariesJob_status_idx" ON "SummariesJob"("status");
CREATE INDEX IF NOT EXISTS "SummariesJob_audienceRoomId_idx" ON "SummariesJob"("audienceRoomId");
CREATE INDEX IF NOT EXISTS "SummariesJob_createdAt_idx" ON "SummariesJob"("createdAt");

-- Async Classifier Job tracking
CREATE TABLE IF NOT EXISTS "ClassifierJob" (
    "id" TEXT NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'PENDING',
    "classifierId" TEXT NOT NULL,
    "audienceRoomId" TEXT NOT NULL,
    "totalProfiles" INTEGER NOT NULL DEFAULT 0,
    "processedProfiles" INTEGER NOT NULL DEFAULT 0,
    "totalPostsClassified" INTEGER NOT NULL DEFAULT 0,
    "error" TEXT,
    "taskToken" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "ClassifierJob_pkey" PRIMARY KEY ("id")
);

CREATE INDEX IF NOT EXISTS "ClassifierJob_status_idx" ON "ClassifierJob"("status");
CREATE INDEX IF NOT EXISTS "ClassifierJob_audienceRoomId_idx" ON "ClassifierJob"("audienceRoomId");
CREATE INDEX IF NOT EXISTS "ClassifierJob_classifierId_idx" ON "ClassifierJob"("classifierId");
CREATE INDEX IF NOT EXISTS "ClassifierJob_createdAt_idx" ON "ClassifierJob"("createdAt");

-- Async Parallel Scraping Job tracking (for batched parallel runs)
CREATE TABLE IF NOT EXISTS "ParallelScrapeJob" (
    "id" TEXT NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'PENDING',
    "audienceRoomId" TEXT,
    "totalBatches" INTEGER NOT NULL DEFAULT 0,
    "completedBatches" INTEGER NOT NULL DEFAULT 0,
    "totalUrls" INTEGER NOT NULL DEFAULT 0,
    "processedUrls" INTEGER NOT NULL DEFAULT 0,
    "batchRunIds" JSONB,
    "error" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "ParallelScrapeJob_pkey" PRIMARY KEY ("id")
);

CREATE INDEX IF NOT EXISTS "ParallelScrapeJob_status_idx" ON "ParallelScrapeJob"("status");
CREATE INDEX IF NOT EXISTS "ParallelScrapeJob_audienceRoomId_idx" ON "ParallelScrapeJob"("audienceRoomId");
CREATE INDEX IF NOT EXISTS "ParallelScrapeJob_createdAt_idx" ON "ParallelScrapeJob"("createdAt");
