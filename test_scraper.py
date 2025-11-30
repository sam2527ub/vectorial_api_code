#!/usr/bin/env python3
"""
Simple test script for async scraper
Usage: python3 test_scraper.py
"""
import requests
import time
import json
import sys

BASE_URL = "http://localhost:8000"

def test_health_check():
    """Test if server is running"""
    try:
        response = requests.get(f"{BASE_URL}/")
        print("✅ Health check:", response.json())
        return True
    except requests.exceptions.ConnectionError:
        print("❌ Server not running! Start it with: python3 main.py")
        return False

def start_scraping_job(linkedin_urls, max_posts=10):
    """Start a scraping job and return job_id"""
    print(f"\n🚀 Starting scraping job for {len(linkedin_urls)} URL(s)...")
    
    # NOTE: Replace with your actual cookies and user agent
    payload = {
        "linkedin_urls": linkedin_urls,
        "max_posts": max_posts,
        "cookies": [
            {
                "domain": ".linkedin.com",
                "name": "li_at",
                "value": "YOUR_COOKIE_VALUE_HERE",  # Replace this!
                "path": "/",
                "secure": True
            }
        ],
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }
    
    try:
        response = requests.post(
            f"{BASE_URL}/api/v1/scrape",
            json=payload,
            timeout=10
        )
        response.raise_for_status()
        job_data = response.json()
        job_id = job_data["job_id"]
        print(f"✅ Job created: {job_id}")
        print(f"   Status: {job_data['status']}")
        print(f"   Message: {job_data.get('message', '')}")
        return job_id
    except requests.exceptions.HTTPError as e:
        print(f"❌ Error: {e}")
        print(f"   Response: {e.response.text}")
        return None
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return None

def poll_job_status(job_id, max_polls=60):
    """Poll job status until completion or failure"""
    print(f"\n⏳ Polling for job status (max {max_polls} polls)...")
    print("   (This may take 5-7 minutes for real scraping)")
    
    poll_count = 0
    while poll_count < max_polls:
        try:
            response = requests.get(
                f"{BASE_URL}/api/v1/scrape/status/{job_id}",
                timeout=10
            )
            response.raise_for_status()
            status_data = response.json()
            
            current_status = status_data["status"]
            poll_count += 1
            
            print(f"\n   Poll #{poll_count}: Status = {current_status}")
            
            if current_status == "COMPLETED":
                print(f"\n✅ Scraping completed!")
                posts_found = status_data.get('posts_found', 0)
                print(f"   Posts found: {posts_found}")
                
                data = status_data.get('data', [])
                if data:
                    print(f"\n   Sample post (first one):")
                    print(f"   {json.dumps(data[0] if isinstance(data, list) else data, indent=2)[:200]}...")
                
                return True
                
            elif current_status == "FAILED":
                error = status_data.get('error', 'Unknown error')
                print(f"\n❌ Scraping failed: {error}")
                return False
                
            else:
                message = status_data.get('message', 'Processing...')
                apify_status = status_data.get('apify_status', '')
                if apify_status:
                    print(f"   Apify status: {apify_status}")
                print(f"   {message}")
                
                # Wait before next poll
                time.sleep(5)  # Poll every 5 seconds
                
        except requests.exceptions.HTTPError as e:
            print(f"❌ HTTP Error: {e}")
            return False
        except Exception as e:
            print(f"❌ Error polling: {e}")
            return False
    
    print(f"\n⏰ Timeout: Reached max polls ({max_polls})")
    return False

def main():
    """Main test function"""
    print("=" * 60)
    print("🧪 Testing Async Scraper API")
    print("=" * 60)
    
    # Step 1: Health check
    if not test_health_check():
        sys.exit(1)
    
    # Step 2: Get LinkedIn URL from user or use default
    if len(sys.argv) > 1:
        linkedin_urls = sys.argv[1:]
    else:
        print("\n📝 Usage: python3 test_scraper.py <linkedin_url1> [linkedin_url2] ...")
        print("   Example: python3 test_scraper.py https://linkedin.com/in/example")
        print("\n⚠️  Note: You need to update cookies in this script first!")
        print("   Edit test_scraper.py and replace 'YOUR_COOKIE_VALUE_HERE'")
        sys.exit(1)
    
    # Step 3: Start scraping job
    job_id = start_scraping_job(linkedin_urls, max_posts=10)
    if not job_id:
        sys.exit(1)
    
    # Step 4: Poll for status
    success = poll_job_status(job_id)
    
    if success:
        print("\n" + "=" * 60)
        print("✅ Test completed successfully!")
        print("=" * 60)
    else:
        print("\n" + "=" * 60)
        print("❌ Test failed or incomplete")
        print("=" * 60)
        sys.exit(1)

if __name__ == "__main__":
    main()


