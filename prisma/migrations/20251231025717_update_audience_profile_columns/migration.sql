-- AlterTable: Update AudienceProfile columns
-- 1. Rename linkedinUrl to profileUrl
ALTER TABLE "AudienceProfile" RENAME COLUMN "linkedinUrl" TO "profileUrl";

-- 2. Add new columns
ALTER TABLE "AudienceProfile" ADD COLUMN "commentsS3Url" TEXT;
ALTER TABLE "AudienceProfile" ADD COLUMN "source" TEXT;

