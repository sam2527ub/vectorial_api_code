-- CreateTable
CREATE TABLE "PostClassifier" (
    "id" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "prompt" TEXT,
    "description" TEXT,
    "labels" JSONB NOT NULL,
    "examples" JSONB,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "PostClassifier_pkey" PRIMARY KEY ("id")
);

