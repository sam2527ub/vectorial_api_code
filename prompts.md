## **1. Profile Posts Summary Prompt**

**System Message:**
```
You are an expert at analyzing LinkedIn posts and generating comprehensive, detailed professional summaries. Write thorough, informative summaries that capture the essence and depth of the person's posting style and content. Always respond with valid JSON only.
```

**User Prompt Template:**
```
Analyze the LinkedIn posts from {profile_name}, who is a {profile_title} at {profile_company}.

Posts ({total_posts} total):
{text_for_analysis}

Generate a comprehensive, detailed analysis:
1. A thorough 5-8 sentence summary that covers:
   - Their current role and company context (mention company stage if evident: Series A/B, startup, growth stage, etc.)
   - Main topics, themes, and subjects they frequently post about
   - Their posting style and tone (technical, thought leadership, personal reflections, etc.)
   - Key insights, opinions, expertise areas, or perspectives they share
   - Notable patterns in content (technical depth, problem-solving focus, industry commentary, etc.)
   - Engagement patterns or community involvement if evident
   - Any unique value propositions or differentiators in their content
   
   Start with "{profile_name} is currently..." or "{profile_name} has..." and write in a natural, engaging way.
   
2. Extract 4-6 key highlights/badges (similar to: "Early + Growth", "Fullstack", "Thought Leader", "Technical Expert", "Series B", "Problem Solver", "Startup Experience", etc.) based on:
   - Company stage mentioned (Series A, B, growth stage, etc.)
   - Technical skills and expertise demonstrated
   - Content themes and posting style (thought leadership, technical depth, etc.)
   - Career patterns or notable experiences mentioned
   - Industry recognition or patterns in their posts
   
3. Identify 10-15 important keywords/phrases that should be highlighted (for keyword highlighting):
   - Technical skills, tools, frameworks, or technologies mentioned
   - Programming languages, platforms, or methodologies
   - Key themes, topics, or subject areas
   - Company names, industries, or domains
   - Concepts, practices, or philosophies discussed

Respond in JSON format only:
{
    "summary": "Detailed 5-8 sentence comprehensive summary starting with the person's name",
    "highlights": ["Highlight 1", "Highlight 2", ...],
    "keywords": ["keyword1", "keyword2", ...]
}
```

**API Configuration:**
- Model: `gpt-4o-mini`
- Max Tokens: `1500`
- Temperature: `0.3`
- Response Format: `json_object`

---

## **2. Profile Comments Summary Prompt**

**System Message:**
```
You are an expert at analyzing LinkedIn comments and engagement patterns. Write thorough, informative summaries that capture how people engage with content through comments. Always respond with valid JSON only.
```

**User Prompt Template:**
```
Analyze the LinkedIn comments made by {profile_name}, who is a {profile_title} at {profile_company}.

This person has commented on {total_comments} posts. For each post, both the original post content and {profile_name}'s comment are provided.

Posts and Comments ({total_comments} total):
{text_for_analysis}

Generate a comprehensive, detailed analysis:
1. A thorough 5-8 sentence summary that covers:
   - The types of posts they engage with (topics, themes, industries)
   - Their commenting style and tone (technical, supportive, thought-provoking, etc.)
   - Key insights, opinions, or expertise they share through comments
   - Patterns in what content they choose to engage with
   - The value they add through their comments (questions, insights, experiences, etc.)
   - Their role as a community member or thought leader through comments
   - Notable themes or subjects they frequently comment on
   
   Start with "{profile_name} actively engages..." or "{profile_name} frequently comments..." and write in a natural, engaging way.
   
2. Extract 4-6 key highlights/badges based on:
   - Types of content they engage with (technical, leadership, industry news, etc.)
   - Commenting style (thoughtful, supportive, technical depth, etc.)
   - Expertise areas demonstrated through comments
   - Community engagement patterns
   
3. Identify 10-15 important keywords/phrases:
   - Technical skills, tools, or technologies mentioned in comments
   - Topics or themes they frequently comment on
   - Industries or domains they engage with
   - Concepts or methodologies discussed

Respond in JSON format only:
{
    "summary": "Detailed 5-8 sentence comprehensive summary starting with the person's name",
    "highlights": ["Highlight 1", "Highlight 2", ...],
    "keywords": ["keyword1", "keyword2", ...]
}
```

**API Configuration:**
- Model: `gpt-4o-mini`
- Max Tokens: `1500`
- Temperature: `0.3`
- Response Format: `json_object`

---

## **3. Group Summary Prompt**

**System Message:**
```
You are an expert at analyzing groups of LinkedIn profiles and generating comprehensive, insightful high-level summaries. Write detailed, informative summaries that capture collective patterns and insights.
```

**User Prompt Template:**
```
Analyze the following group of {total_profiles} profiles who work at {company_type}.

Total posts analyzed: {total_posts}
Companies represented: {company_list}

Individual Profile Summaries:
{combined_summaries}

Generate a comprehensive high-level summary (6-10 sentences) that covers:
1. Overall themes and patterns across all profiles in this group
2. Common topics, technologies, or expertise areas shared among them
3. Company culture and stage characteristics evident from their posts
4. Professional focus areas (e.g., technical depth, thought leadership, product development)
5. Industry trends or insights that emerge from the collective content
6. Unique characteristics or differentiators of this group
7. Common posting styles or engagement patterns
8. Key value propositions or strengths evident across the group

Write in a natural, engaging way that provides insights into this collective group of professionals from {company_type}.

Respond with ONLY the summary text, no JSON or formatting.
```

**API Configuration:**
- Model: `gpt-4o-mini`
- Max Tokens: `1200`
- Temperature: `0.3`
- Response Format: `text` (not JSON)