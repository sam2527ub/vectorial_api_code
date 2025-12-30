step 1
/api/search/parallel        Search Parallel Stream
/api/v1/audience-rooms      Create Audience Room

step 2
/api/v1/scrape      Trigger Scraping
/api/v1/scrape/status/{job_id}  Get Scrape Status

step 3
/api/v1/audience-rooms/{audience_room_id}/generate-summaries            Generate Profile Summaries
/api/v1/audience-rooms/{audience_room_id}/generate-group-summary        Generate Group Summary

step 4
POST
/api/classifier/run       Run Classifier

step 5
Ingestion

