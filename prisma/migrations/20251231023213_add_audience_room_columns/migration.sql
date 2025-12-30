-- AlterTable
ALTER TABLE "AudienceRoom" ADD COLUMN "source" TEXT NOT NULL DEFAULT 'Linkedin';
ALTER TABLE "AudienceRoom" ADD COLUMN "query" TEXT;
ALTER TABLE "AudienceRoom" ADD COLUMN "indexesS3Url" TEXT;

-- Update existing rows to have 'Linkedin' as source (already handled by default, but explicit update for safety)
UPDATE "AudienceRoom" SET "source" = 'Linkedin' WHERE "source" IS NULL;

