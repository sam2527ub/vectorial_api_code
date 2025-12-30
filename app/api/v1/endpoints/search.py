"""Search endpoints."""
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime
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
    - name: Full name of the person
    - age: Calculated from birth_date
    - current_company: Current job company name
    - current_location: Current location
    - total_years_experience: Calculated years of experience (excluding internships)
    - industry: Industry
    - education: Most recent/highest education (e.g., "Bachelors from Stanford University (Computer Science)")
    - linkedin_profile_url: LinkedIn profile URL
    
    Note: If searching for tech roles (engineer, developer, etc.) without specifying industry,
    results may include engineers working in non-tech industries (retail, real estate, etc.).
    To get more relevant results, add industry filter: ["Technology", "Computer Software", "Internet"]
    """
    sql_query = build_pdl_sql(payload)
    logger.info(f"Executing Search SQL: {sql_query}")

    params = {
        'sql': sql_query,
        'dataset': 'resume',
        'size': payload.limit,
        'pretty': True
    }

    try:
        response = pdl_client.person.search(**params).json()
        data = response.get('data', [])
        
        # Post-processing: Calculate Experience Years and filter to only required fields
        processed_profiles = []
        for person in data:
            # Calculate experience years
            years = calculate_experience_years(person.get('experience', []))
            
            # Calculate age - try multiple sources
            age = None
            
            # Method 1: Try birth_date (most accurate)
            if person.get('birth_date'):
                try:
                    birth_date = parse(person.get('birth_date'))
                    age = (datetime.now() - birth_date).days // 365
                except (ParserError, ValueError, TypeError):
                    pass
            
            # Method 2: Try inferred_age if available (PDL sometimes provides this)
            if age is None and person.get('inferred_age'):
                try:
                    age = int(person.get('inferred_age'))
                except (ValueError, TypeError):
                    pass
            
            # Method 3: Estimate from education graduation year (less accurate)
            if age is None and person.get('education'):
                try:
                    # Get most recent graduation year
                    graduation_years = []
                    for edu in person.get('education', []):
                        end_date = edu.get('end_date')
                        if end_date:
                            # Try to extract year
                            if len(end_date) >= 4:
                                year = int(end_date[:4])
                                graduation_years.append(year)
                    
                    if graduation_years:
                        # Assume typical graduation age: 22 for Bachelor's, 24 for Master's
                        most_recent_graduation = max(graduation_years)
                        years_since_graduation = datetime.now().year - most_recent_graduation
                        # Estimate: graduated at 22-24, add years since
                        estimated_age = 23 + years_since_graduation
                        if 18 <= estimated_age <= 80:  # Reasonable age range
                            age = estimated_age
                except (ValueError, TypeError, KeyError):
                    pass
            
            # Extract education information
            education_info = None
            if person.get('education') and len(person.get('education', [])) > 0:
                # Get the most recent/highest level education
                education_list = person.get('education', [])
                # Sort by end_date (most recent first) or start_date, prioritizing higher degrees
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
                
                # Format education string
                degrees = most_recent_edu.get('degrees', [])
                school = most_recent_edu.get('school', {})
                school_name = school.get('name', '') if isinstance(school, dict) else str(school) if school else ''
                majors = most_recent_edu.get('majors', [])
                
                # Clean and normalize degrees (remove duplicates and verbose forms)
                cleaned_degrees = []
                degree_seen = set()
                for deg in degrees:
                    deg_str = str(deg).strip()
                    # Normalize common degree names
                    deg_lower = deg_str.lower()
                    if 'bachelor' in deg_lower and 'bachelors' not in deg_lower:
                        deg_str = 'Bachelors'
                    elif 'master' in deg_lower and 'masters' not in deg_lower:
                        deg_str = 'Masters'
                    elif 'phd' in deg_lower or 'doctorate' in deg_lower:
                        deg_str = 'PhD'
                    
                    # Avoid duplicates
                    if deg_str.lower() not in degree_seen:
                        cleaned_degrees.append(deg_str)
                        degree_seen.add(deg_str.lower())
                
                # Build education string: "Degree from School (Major)" or "School (Major)" if no degree
                edu_parts = []
                
                # Add degree if available
                if cleaned_degrees:
                    # Use the highest/most common degree
                    degree = cleaned_degrees[0]
                    edu_parts.append(degree)
                
                # Add school
                if school_name:
                    if edu_parts:
                        edu_parts.append(f"from {school_name}")
                    else:
                        # If no degree, just use school name
                        edu_parts.append(school_name)
                
                # Add majors
                if majors:
                    majors_str = ', '.join(majors)
                    edu_parts.append(f"({majors_str})")
                
                education_info = ' '.join(edu_parts) if edu_parts else None
            
            # Extract only required fields
            profile = {
                "name": person.get('full_name') or f"{person.get('first_name', '')} {person.get('last_name', '')}".strip() or None,
                "age": age,
                "current_company": person.get('job_company_name'),
                "current_location": person.get('location_name'),
                "total_years_experience": years,  # Calculated field
                "industry": person.get('industry'),
                "education": education_info,
                "linkedin_profile_url": person.get('linkedin_url')
            }
            
            processed_profiles.append(profile)

        return {
            "count": len(processed_profiles),
            "sql_generated": sql_query,
            "profiles": processed_profiles
        }
    except Exception as e:
        logger.error(f"Search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

