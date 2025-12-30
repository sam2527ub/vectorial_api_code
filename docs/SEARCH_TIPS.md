# Search Tips - Getting Better Results

## Understanding Job Title vs Industry

When you search for "backend engineer", you're searching by **job title**, not **industry**. This means you'll get:
- ✅ Backend engineers at tech companies (Google, Microsoft)
- ✅ Backend engineers at retail companies (Walmart, Target)
- ✅ Backend engineers at real estate companies
- ✅ Backend engineers at any company that has that role

**This is correct behavior** - a backend engineer CAN work at a retail company. But if you want more relevant results, you should filter by industry.

## Recommended Industry Filters for Tech Roles

When searching for engineering/developer roles, add these industries:

```json
{
  "titles": ["backend engineer"],
  "industries": [
    "Technology",
    "Computer Software",
    "Internet",
    "Information Technology and Services",
    "Computer & Network Security",
    "Telecommunications"
  ]
}
```

## Common Industry Values

### Tech Industries
- `"Technology"`
- `"Computer Software"`
- `"Internet"`
- `"Information Technology and Services"`
- `"Computer & Network Security"`
- `"Telecommunications"`
- `"Semiconductors"`

### Other Industries
- `"Financial Services"`
- `"Banking"`
- `"Retail"`
- `"Real Estate"`
- `"Healthcare"`
- `"Education"`
- `"Entertainment"`
- `"Gambling & Casinos"`

## Example: Better Search for Backend Engineers

### Without Industry Filter (Gets All Industries)
```json
{
  "titles": ["backend engineer"],
  "limit": 10
}
```
**Result**: Backend engineers from all industries (tech, retail, real estate, etc.)

### With Industry Filter (Tech Only)
```json
{
  "titles": ["backend engineer"],
  "industries": ["Technology", "Computer Software", "Internet"],
  "limit": 10
}
```
**Result**: Only backend engineers working at tech companies

## Best Practices

1. **Always specify industry** when searching for tech roles
2. **Combine filters** for better results:
   ```json
   {
     "titles": ["backend engineer"],
     "skills": ["Python", "Node.js"],
     "industries": ["Technology", "Computer Software"],
     "locations": ["United States"]
   }
   ```
3. **Use job_roles** for broader matching:
   ```json
   {
     "job_roles": ["Engineer", "Developer"],
     "industries": ["Technology"]
   }
   ```

## Why This Happens

PDL's search works like this:
- **Job Title** = What the person does (e.g., "Backend Engineer")
- **Industry** = What the company does (e.g., "Retail", "Real Estate")

A person can have a tech job title but work at a non-tech company. That's why you see:
- Backend Engineer at Walmart (retail industry)
- Backend Engineer at Real Estate Company (real estate industry)

To get only tech companies, **always add industry filter**!

