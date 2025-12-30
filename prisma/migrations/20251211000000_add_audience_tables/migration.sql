-- CreateTable
CREATE TABLE "AudienceRoom" (
    "id" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "descriptionS3Url" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "AudienceRoom_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "AudienceProfile" (
    "id" TEXT NOT NULL,
    "audienceRoomId" TEXT NOT NULL,
    "profileName" TEXT NOT NULL,
    "linkedinUrl" TEXT NOT NULL,
    "profileDescriptionS3Url" TEXT,
    "postsS3Url" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "AudienceProfile_pkey" PRIMARY KEY ("id")
);

-- AddForeignKey
ALTER TABLE "AudienceProfile" ADD CONSTRAINT "AudienceProfile_audienceRoomId_fkey" FOREIGN KEY ("audienceRoomId") REFERENCES "AudienceRoom"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- CreateIndex
CREATE INDEX "AudienceProfile_audienceRoomId_idx" ON "AudienceProfile"("audienceRoomId");


