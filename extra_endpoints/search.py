"""Extra endpoint: Search profiles using PDL API."""
from datetime import datetime
from typing import List, Optional, Dict, Any
from dateutil.parser import parse, ParserError
from fastapi import APIRouter, HTTPException

from app.models.schemas import SearchFilters
from app.config import pdl_client, logger
from app.utils.helpers import build_pdl_sql, calculate_experience_years

router = APIRouter()


@router.post("/api/v1/search")
async def search_profiles(payload: SearchFilters):
    """
    Search for profiles using PDL API.
    Returns filtered profile information with only:
    - name, age, current_company, current_location, total_years_experience,
      industry, education, linkedin_profile_url
    """
    logger.info(f"=== SEARCH PROFILES REQUEST START ===")
    logger.info(f"Request: limit={payload.limit}, titles={len(payload.titles)}, skills={len(payload.skills)}, locations={len(payload.locations)}")

    if not pdl_client:
        logger.error("PDL client not initialized")
        raise HTTPException(status_code=503, detail="PDL client not initialized")

    sql_query = build_pdl_sql(payload)
    logger.info(f"Generated SQL query: {sql_query}")

    params = {
        'sql': sql_query,
        'dataset': 'resume',
        'size': payload.limit,
        'pretty': True
    }

    try:
        logger.info(f"Calling PDL API with limit={payload.limit}")
        response = pdl_client.person.search(**params).json()
        data = response.get('data', [])
        logger.info(f"PDL API returned {len(data)} profiles")

        logger.info(f"Processing {len(data)} profiles")
        processed_profiles = []
        for idx, person in enumerate(data):
            if idx % 10 == 0:
                logger.info(f"Processing profile {idx+1}/{len(data)}")
            years = calculate_experience_years(person.get('experience', []))
            age = None

            if person.get('birth_date'):
                try:
                    birth_date = parse(person.get('birth_date'))
                    age = (datetime.now() - birth_date).days // 365
                except (ParserError, ValueError, TypeError):
                    pass
            if age is None and person.get('inferred_age'):
                try:
                    age = int(person.get('inferred_age'))
                except (ValueError, TypeError):
                    pass
            if age is None and person.get('education'):
                try:
                    graduation_years = []
                    for edu in person.get('education', []):
                        end_date = edu.get('end_date')
                        if end_date and len(end_date) >= 4:
                            graduation_years.append(int(end_date[:4]))
                    if graduation_years:
                        most_recent_graduation = max(graduation_years)
                        years_since_graduation = datetime.now().year - most_recent_graduation
                        estimated_age = 23 + years_since_graduation
                        if 18 <= estimated_age <= 80:
                            age = estimated_age
                except (ValueError, TypeError, KeyError):
                    pass

            education_info = None
            if person.get('education') and len(person.get('education', [])) > 0:
                education_list = person.get('education', [])

                def get_education_priority(edu):
                    degrees = edu.get('degrees', [])
                    degree_priority = {
                        'PhD': 4, 'Doctorate': 4, 'Ph.D.': 4,
                        'Masters': 3, 'Master': 3, 'Master of Science': 3, 'Master of Arts': 3,
                        'Bachelors': 2, 'Bachelor': 2, 'Bachelor of Science': 2, 'Bachelor of Arts': 2,
                        'Associates': 1, 'Associate': 1
                    }
                    max_priority = 0
                    for deg in degrees:
                        for key, priority in degree_priority.items():
                            if key.lower() in str(deg).lower():
                                max_priority = max(max_priority, priority)
                                break
                    return (max_priority, edu.get('end_date') or edu.get('start_date') or '0000')

                sorted_education = sorted(education_list, key=get_education_priority, reverse=True)
                most_recent_edu = sorted_education[0]
                degrees = most_recent_edu.get('degrees', [])
                school = most_recent_edu.get('school', {})
                school_name = school.get('name', '') if isinstance(school, dict) else str(school) if school else ''
                majors = most_recent_edu.get('majors', [])

                cleaned_degrees = []
                degree_seen = set()
                for deg in degrees:
                    deg_str = str(deg).strip()
                    deg_lower = deg_str.lower()
                    if 'bachelor' in deg_lower and 'bachelors' not in deg_lower:
                        deg_str = 'Bachelors'
                    elif 'master' in deg_lower and 'masters' not in deg_lower:
                        deg_str = 'Masters'
                    elif 'phd' in deg_lower or 'doctorate' in deg_lower:
                        deg_str = 'PhD'
                    if deg_str.lower() not in degree_seen:
                        cleaned_degrees.append(deg_str)
                        degree_seen.add(deg_str.lower())

                edu_parts = []
                if cleaned_degrees:
                    edu_parts.append(cleaned_degrees[0])
                if school_name:
                    if edu_parts:
                        edu_parts.append(f"from {school_name}")
                    else:
                        edu_parts.append(school_name)
                if majors:
                    edu_parts.append(f"({', '.join(majors)})")
                education_info = ' '.join(edu_parts) if edu_parts else None

            profile = {
                "name": person.get('full_name') or f"{person.get('first_name', '')} {person.get('last_name', '')}".strip() or None,
                "age": age,
                "current_company": person.get('job_company_name'),
                "current_location": person.get('location_name'),
                "total_years_experience": years,
                "industry": person.get('industry'),
                "education": education_info,
                "linkedin_profile_url": person.get('linkedin_url')
            }
            processed_profiles.append(profile)

        logger.info(f"Successfully processed {len(processed_profiles)} profiles")
        logger.info(f"=== SEARCH PROFILES REQUEST SUCCESS ===")

        return {
            "count": len(processed_profiles),
            "sql_generated": sql_query,
            "profiles": processed_profiles
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"=== ERROR in search_profiles ===")
        logger.error(f"Search error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
