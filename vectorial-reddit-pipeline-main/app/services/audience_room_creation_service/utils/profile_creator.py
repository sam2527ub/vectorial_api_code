"""Profile creation and S3 upload logic."""
import uuid
from typing import Dict, Any, List
from app.config import logger
from app.utils.s3_utils import upload_json_to_s3, get_s3_key_for_audience
from app.services.audience_room_creation_service.clients.storage.factory import get_storage_client
from app.services.audience_room_creation_service.utils.username_extractor import UsernameExtractor


class ProfileCreator:
    """Creates profiles and uploads to S3."""
    
    def __init__(self):
        """Initialize profile creator."""
        self.username_extractor = UsernameExtractor()
        self.storage_client = get_storage_client()
    
    def create_profiles(
        self,
        users: List[Dict[str, Any]],
        user_activities_map: Dict[str, List[Dict[str, Any]]],
        room_id: str,
        enterprise_name: str,
        source: str
    ) -> List[Dict[str, Any]]:
        """Create profiles and upload to S3."""
        profile_creates = []
        total_users = len(users)
        
        for idx, user in enumerate(users):
            username = self.username_extractor.extract_from_user(user)
            if not username:
                logger.warning(f"Skipping user {idx+1}/{total_users} - no username")
                continue
            
            profile_id = str(uuid.uuid4())
            user_activities = user_activities_map.get(username, [])
            
            posts = [a for a in user_activities if a.get("type") == "post"]
            comments = [a for a in user_activities if a.get("type") == "comment"]
            
            # Upload profile.json
            profile_key = get_s3_key_for_audience(
                room_id,
                f"profiles/{profile_id}/profile.json",
                enterprise_name,
                source
            )
            profile_data = {
                "profile_id": profile_id,
                "audience_room_id": room_id,
                "username": user.get("username"),
                "userId": user.get("userId"),
                "userUrl": user.get("userUrl"),
                "postCount": user.get("postCount", 0),
                "commentCount": user.get("commentCount", 0),
                "firstActivity": user.get("firstActivity"),
                "lastActivity": user.get("lastActivity"),
            }
            profile_description_url = upload_json_to_s3(
                profile_key,
                profile_data,
                s3_client=self.storage_client.get_raw_client(),
                s3_bucket=self.storage_client.get_bucket_name(),
                s3_region=self.storage_client.get_region()
            )
            
            # Upload comments.json if any
            comments_url = None
            if comments:
                comments_key = get_s3_key_for_audience(
                    room_id,
                    f"profiles/{profile_id}/comments.json",
                    enterprise_name,
                    source
                )
                comments_url = upload_json_to_s3(
                    comments_key,
                    comments,
                    s3_client=self.storage_client.get_raw_client(),
                    s3_bucket=self.storage_client.get_bucket_name(),
                    s3_region=self.storage_client.get_region()
                )
            
            # Upload posts.json if any
            posts_url = None
            if posts:
                posts_key = get_s3_key_for_audience(
                    room_id,
                    f"profiles/{profile_id}/posts.json",
                    enterprise_name,
                    source
                )
                posts_url = upload_json_to_s3(
                    posts_key,
                    posts,
                    s3_client=self.storage_client.get_raw_client(),
                    s3_bucket=self.storage_client.get_bucket_name(),
                    s3_region=self.storage_client.get_region()
                )
            
            profile_creates.append({
                "id": profile_id,
                "profileName": username,
                "profileUrl": user.get("userUrl") or f"https://www.reddit.com/user/{username}/",
                "profileDescriptionS3Url": profile_description_url,
                "postsS3Url": posts_url,
                "commentsS3Url": comments_url,
                "source": source,
            })
            
            # Log progress every 10 users
            if (idx + 1) % 10 == 0 or (idx + 1) == total_users:
                logger.info(
                    f"Processed user {idx+1}/{total_users} ({username}), "
                    f"Total profiles: {len(profile_creates)}"
                )
        
        return profile_creates
