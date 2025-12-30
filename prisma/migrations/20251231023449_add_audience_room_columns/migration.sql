-- AlterTable: Add new columns to AudienceRoom
ALTER TABLE "AudienceRoom" ADD COLUMN "source" TEXT;
ALTER TABLE "AudienceRoom" ADD COLUMN "query" TEXT;
ALTER TABLE "AudienceRoom" ADD COLUMN "indexesS3Url" TEXT;

-- Update existing rows to have 'Linkedin' as source
UPDATE "AudienceRoom" SET "source" = 'Linkedin' WHERE "source" IS NULL;

