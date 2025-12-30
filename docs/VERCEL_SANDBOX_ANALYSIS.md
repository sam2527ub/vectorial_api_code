# Vercel Sandbox vs AWS Fargate - Analysis for Parallel Search API

## Executive Summary

**Recommendation: ✅ Continue using AWS Fargate (DO NOT switch to Vercel Sandbox)**

Given that you've already moved `/api/search/parallel` to AWS Fargate due to timeout issues, **stick with Fargate**. Vercel Sandbox is not designed to replace containerized HTTP servers like Fargate.

---

## Current Situation

### ⚠️ Parallel Search API (`/api/search/parallel`) - Moved to Fargate
- **Current Deployment**: AWS Fargate (moved from Vercel Fluid)
- **Reason for Migration**: Function execution time exceeded Vercel serverless limits
- **Pattern**: Server-Sent Events (SSE) streaming with parallel Apify scraping
- **Challenge**: Even with SSE streaming, the function itself was timing out

**Why it needed Fargate:**
- SSE streaming requires long-lived connections (potentially 5-30+ minutes)
- Parallel Apify API calls for profile enrichment add processing time
- Vercel serverless functions have max duration limits (60s Hobby, ~300s Pro/Enterprise)
- Function execution time was exceeding these limits despite streaming architecture

### ✅ Scrape API (`/api/v1/scrape`)
- **Pattern**: Async job queue
- **Status**: ✅ No timeout issues (already fixed)
- **How it works**:
  - Creates job in database (< 1 second)
  - Starts Apify actor asynchronously (non-blocking)
  - Returns `job_id` immediately
  - Frontend polls `/api/v1/scrape/status/{job_id}`
  - Apify runs externally (5-7 minutes on Apify's platform)

**Why it doesn't timeout:**
- Returns immediately (< 1 second)
- Heavy work happens on Apify's platform, not Vercel
- Status polling endpoints are fast (< 1 second each)

### ✅ Other Apify APIs
- **Status**: ✅ No timeout concerns
- **Reason**: All Apify operations run on Apify's platform, not on Vercel functions

---

## Vercel Sandbox vs AWS Fargate Comparison

### AWS Fargate (Current Solution)
**Characteristics:**
- ✅ Containerized application hosting
- ✅ Full control over runtime environment
- ✅ Can run indefinitely (no hard time limits)
- ✅ Supports long-lived HTTP connections (SSE, WebSockets)
- ✅ Mature, production-ready service
- ✅ Flexible resource allocation (CPU, memory)
- ✅ Can handle concurrent requests efficiently
- ✅ Direct FastAPI/HTTP server deployment
- ✅ Easy to scale (ECS auto-scaling)
- ✅ Integrated with AWS ecosystem

**Limitations:**
- Requires container management
- More infrastructure overhead
- Need to manage AWS resources
- Cold starts possible (but can be mitigated)

**Cost:**
- Pay for running containers (CPU + memory)
- Can be optimized with auto-scaling
- Predictable pricing model

### Vercel Sandbox
**Characteristics:**
- ✅ Designed for isolated code execution
- ✅ Long-running task support (45 min Hobby, 5 hours Pro/Enterprise)
- ✅ Ephemeral, isolated environments
- ✅ Supports Node.js, Python runtimes
- ✅ Can expose ports for HTTP access
- ❌ **Not designed as a replacement for containerized HTTP servers**
- ❌ Currently in beta (may lack stability)
- ❌ Primarily for code execution, not persistent API hosting
- ❌ Less mature than Fargate

**Limitations:**
- Maximum runtime: 5 hours (Pro/Enterprise) - may not be enough for very long searches
- Still has time limits (even if longer)
- Beta status means potential instability
- Not optimized for persistent HTTP server workloads
- May have connection limits or other constraints
- Less control over infrastructure

**Cost:**
- Pricing may be different from serverless functions
- Pay per execution time
- May be more expensive for persistent workloads

---

## Vercel Sandbox vs Serverless Functions (For Reference)

### Serverless Functions (Current)
**Limits:**
- Default timeout: 10s (Hobby), 15s (Pro/Enterprise)
- Maximum configurable: ~60s (Hobby), ~300s (5 min) (Pro/Enterprise)

**Use Cases:**
- HTTP API endpoints
- Request/response pattern
- Fast execution (< 5 minutes)
- Stateless operations

**Cost:**
- Pay per invocation
- Efficient for short-lived functions

### Vercel Sandbox
**Limits:**
- Maximum runtime: 45 min (Hobby), 5 hours (Pro/Enterprise)
- Default: 5-10 minutes (configurable)
- Can extend timeout programmatically

**Use Cases:**
- Isolated code execution environments
- Running arbitrary user code
- AI model inference directly in Vercel
- Long-running AI agent workflows
- Code generation and testing
- Autonomous agent tasks

**Cost:**
- More expensive than serverless functions
- Designed for compute-intensive, long-running isolated tasks

---

## Why NOT Switch from Fargate to Vercel Sandbox?

### 1. ❌ Wrong Tool for HTTP Server Hosting
**Vercel Sandbox is NOT designed to replace containerized HTTP servers:**
- Sandbox is for **code execution in isolation** (like running scripts, tests, AI agents)
- Fargate is for **hosting persistent HTTP servers** (like your FastAPI app)
- Your `/api/search/parallel` endpoint is an HTTP server endpoint, not an isolated code execution task

### 2. ✅ Fargate is Perfect for Your Use Case
**Fargate excels at:**
- Hosting long-running HTTP servers (FastAPI, Flask, Express, etc.)
- Maintaining persistent SSE connections (hours if needed)
- Handling concurrent requests efficiently
- Auto-scaling based on traffic
- Production-ready reliability

**Your Parallel Search API needs:**
- ✅ Long-lived SSE connections (5-30+ minutes)
- ✅ HTTP server hosting (FastAPI)
- ✅ Concurrent request handling
- ✅ Persistent service availability
- ✅ No hard time limits

### 3. ⚠️ Sandbox Limitations
**Why Sandbox is problematic:**
- Still has maximum runtime limits (5 hours max for Pro/Enterprise)
- Beta status = potential instability for production workloads
- Not optimized for persistent HTTP server hosting
- May have connection limits or other constraints you haven't hit with Fargate
- Less control over infrastructure and scaling

### 4. 💰 Cost & Complexity
**Fargate:**
- Predictable container pricing
- You control resource allocation
- Can optimize costs with auto-scaling
- Mature ecosystem with good tooling

**Sandbox:**
- New pricing model (may be more expensive for persistent workloads)
- Less flexibility in resource management
- Additional migration effort
- Beta = potential unexpected costs/issues

### 5. 🏗️ Architecture Fit
**Current Fargate Setup:**
```
Frontend → AWS Fargate (FastAPI) → Parallel AI API
                                ↓
                            Apify API
```
- Standard HTTP server pattern
- Well-understood architecture
- Easy to debug and monitor
- Standard container deployment

**Sandbox Pattern:**
```
Frontend → ??? → Sandbox (code execution) → Parallel AI API
```
- Less clear how HTTP routing works
- Not designed for persistent servers
- Unclear request handling model

---

## When WOULD You Use Vercel Sandbox? (For Reference)

### Appropriate Use Cases:

1. **Running AI Models Directly**
   - If you wanted to run LLM inference in Vercel (instead of calling external APIs)
   - Example: Running Claude/OpenAI models directly in your infrastructure

2. **User-Submitted Code Execution**
   - If users could submit code that you need to run
   - Example: Online code editor, testing framework

3. **Long-Running Agent Workflows**
   - Autonomous agents that need to run for hours
   - Example: Web scraping agents, data processing pipelines

4. **Code Generation Workflows**
   - Generating and testing code dynamically
   - Example: AI code generation with validation

### Your Current APIs Don't Fit These Patterns

---

## Recommendations

### ✅ Continue Using AWS Fargate

**For Parallel Search API (`/api/search/parallel`):**

1. **Keep your current Fargate deployment** - It's the right tool for the job
2. **Fargate is optimized for:**
   - Long-running HTTP servers (like your FastAPI app)
   - SSE streaming with persistent connections
   - Concurrent request handling
   - Auto-scaling based on demand

3. **Optimize Fargate setup (if not already):**
   ```yaml
   # Example ECS Task Definition optimizations
   - Set appropriate CPU/memory based on workload
   - Enable auto-scaling based on request count
   - Use Application Load Balancer for routing
   - Configure health checks for the FastAPI endpoint
   - Consider Fargate Spot for cost savings (if suitable)
   ```

### ❌ Do NOT Switch to Vercel Sandbox Because:

1. **Wrong use case**: Sandbox is for code execution, not HTTP server hosting
2. **Fargate is better suited**: Container hosting is exactly what you need
3. **Stability**: Fargate is production-ready; Sandbox is still beta
4. **No time limits**: Fargate can run indefinitely; Sandbox has 5-hour max
5. **Migration effort**: Unnecessary work with unclear benefits
6. **Cost uncertainty**: Sandbox pricing model may not be favorable for persistent servers

### 📊 Alternative: Hybrid Approach (If Needed)

If you want some endpoints on Vercel and others on Fargate:

**Keep on Vercel (serverless functions):**
- `/api/v1/scrape` - Returns immediately with job_id
- Status endpoints - Fast, stateless
- Other short-running endpoints

**Keep on Fargate:**
- `/api/search/parallel` - Long-running SSE streaming
- Any other endpoints requiring persistent connections

---

## Summary Table

| Solution | Runtime Limit | HTTP Server | Production Ready | Cost Predictability | Best For |
|----------|---------------|-------------|------------------|---------------------|----------|
| **AWS Fargate** ✅ | Unlimited | ✅ Yes | ✅ Yes | ✅ Yes | HTTP servers, SSE, WebSockets |
| **Vercel Sandbox** ❌ | 5 hours (max) | ⚠️ Limited | ⚠️ Beta | ⚠️ Uncertain | Code execution, AI agents |
| **Vercel Serverless** | ~300s (max) | ✅ Yes | ✅ Yes | ✅ Yes | Short-running APIs |

---

## Can You Send HTTP Requests to Vercel Sandbox? ⚠️

**Yes, BUT it's not like Lambda!** Here's how it actually works:

### How Sandbox HTTP Access Works

1. **Create Sandbox**: You programmatically create a sandbox environment
2. **Expose Port**: Specify ports (e.g., `ports: [3000]`) when creating the sandbox
3. **Run HTTP Server**: Start your HTTP server (FastAPI, Express, etc.) inside the sandbox
4. **Get Public Domain**: Call `sandbox.domain(3000)` to get a public URL like `https://xxx.vercel-sandbox.com`
5. **Send Requests**: You can send HTTP requests to that URL

**Example:**
```typescript
const sandbox = await Sandbox.create({
  source: { url: 'https://github.com/your/repo.git', type: 'git' },
  ports: [3000],
  timeout: ms('1h'),
  runtime: 'python3.13',
});

// Start FastAPI server in sandbox
await sandbox.runCommand({
  cmd: 'python',
  args: ['-m', 'uvicorn', 'main:app', '--port', '3000'],
  detached: true,
});

// Get public URL
const url = sandbox.domain(3000);
// Send requests to: https://xxx.vercel-sandbox.com/api/search/parallel
```

### Key Differences from Lambda & Fargate

| Feature | AWS Lambda | Vercel Sandbox | AWS Fargate |
|---------|------------|----------------|-------------|
| **Invocation Model** | Stateless, per-request | Persistent container | Persistent container |
| **HTTP Access** | Direct invoke URL | Public domain URL | Load balancer URL |
| **Runtime Limits** | 15 min max | 5 hours max (Pro) | Unlimited |
| **Scaling** | Automatic (per request) | Manual (create sandbox) | Auto-scaling (ECS) |
| **State** | Stateless | Persistent | Persistent |
| **Lifecycle** | Per-request | Timeout-based | Until stopped |
| **Cost Model** | Pay per invocation | Pay per runtime | Pay per container |

### Why This Doesn't Work Well for Your Use Case

1. **❌ No Auto-Scaling**: You manually create sandboxes, not automatic per-request
2. **❌ Timeout Limits**: Still has 5-hour max (Pro/Enterprise), may not be enough
3. **❌ Management Overhead**: You need to:
   - Create sandbox before use
   - Manage sandbox lifecycle
   - Handle sandbox timeouts
   - Create new sandboxes when old ones expire
4. **❌ Not Like Lambda**: Lambda is stateless - each request spawns a new execution. Sandbox is a persistent container that you need to manage.
5. **❌ Beta Limitations**: Still in beta, may have unexpected behavior
6. **❌ Complex Setup**: Requires SDK/programmatic creation, not simple HTTP endpoint

### Comparison with Your Current Fargate Setup

**Fargate (Current):**
```
Frontend → ALB → Fargate Container (FastAPI)
                    ↓ (runs indefinitely, auto-scales)
```
- ✅ Always available
- ✅ Auto-scales based on load
- ✅ Standard HTTP endpoint
- ✅ No time limits

**Sandbox (Would be):**
```
Frontend → Your App → SDK → Create Sandbox → Run FastAPI → Get URL → Send Request
                          ↓ (5 hour timeout, manual management)
```
- ❌ Need to create sandbox first
- ❌ Manual scaling
- ❌ Timeout limits
- ❌ More complex architecture

---

## Conclusion

**✅ STICK WITH AWS FARGATE for your Parallel Search API!**

You made the right architectural decision by moving to Fargate. Here's why:

1. **Fargate is designed for your exact use case**: Hosting HTTP servers with long-running connections
2. **No arbitrary time limits**: Unlike Sandbox's 5-hour max, Fargate can run indefinitely
3. **Production-ready**: Fargate is mature, stable, and battle-tested
4. **Better fit**: Container hosting is the standard solution for FastAPI HTTP servers
5. **Simpler architecture**: Direct HTTP endpoint vs. programmatic sandbox creation
6. **Auto-scaling**: Fargate auto-scales; Sandbox requires manual management
7. **Sandbox limitations**: While you CAN send HTTP requests to Sandbox, it's not designed for persistent API hosting - it's more for temporary code execution environments

**Your current Fargate deployment is the optimal solution. Don't switch to Vercel Sandbox.** 🎯

---

## TL;DR - Can You Send HTTP Requests to Sandbox?

**Yes**, you can send HTTP requests to Sandbox, but:
- It's **NOT like Lambda** (stateless per-request)
- It's **like a temporary container** with a public URL
- Requires **programmatic creation** and **lifecycle management**
- Has **timeout limits** (5 hours max)
- **No auto-scaling** - you manage sandboxes manually

**For your FastAPI SSE endpoint, Fargate is still the better choice.**

---

## Additional Notes

If you're experiencing issues with Fargate, consider:
- **Optimizing container resources** (CPU/memory allocation)
- **Implementing connection pooling** for Apify API calls
- **Adding caching** for frequently accessed profile data
- **Monitoring and alerting** for performance bottlenecks
- **Load testing** to identify optimal scaling settings

These optimizations will be more valuable than switching platforms.

---

## Is Sandbox Just "Longer Running Lambdas"? 🤔

**Short Answer: No. They're fundamentally different execution models.**

### Lambda Model (Stateless, Event-Driven)
```
Request → Lambda invokes handler → Executes → Returns → Container may be reused or destroyed
```
- **Stateless**: Each invocation is independent
- **Event-driven**: Lambda invokes your handler function when triggered
- **Automatic scaling**: Lambda creates/destroys containers automatically
- **Per-request execution**: Function handler runs per request
- **No persistent state**: State doesn't persist between invocations (by design)
- **Runtime limit**: 15 minutes max per invocation

### Sandbox Model (Stateful, Persistent Container)
```
Create Sandbox → Run commands/start server → Container persists → Send requests → Container stays alive
```
- **Stateful**: Container persists and maintains state
- **Manual lifecycle**: YOU create/manage/stop the sandbox
- **Persistent environment**: Container runs until timeout or you stop it
- **No automatic scaling**: You create multiple sandboxes manually if needed
- **Runtime limit**: 5 hours max (but container persists during that time)

### Key Conceptual Difference

**Lambda is like:** A restaurant where each customer (request) gets a waiter (function instance) who serves them and then becomes available for the next customer. Waiters may be reused, but each service is independent.

**Sandbox is like:** Renting a kitchen (container) for a fixed period. You set it up, it stays running, and you can cook multiple meals (process requests) in it. But YOU manage when to rent it, when it expires, and if you need more kitchens.

### Visual Comparison

#### AWS Lambda Execution Flow:
```
Request 1 → [Handler executes] → Response → Container may be reused
Request 2 → [Same handler executes] → Response → Container may be reused  
Request 3 → [Handler executes in NEW container] → Response
```
- Lambda manages container lifecycle
- Containers are stateless (no shared state between requests)
- Auto-scales automatically

#### Vercel Sandbox Execution Flow:
```
1. Create Sandbox → Container created
2. Run "start server" command → Server starts in container
3. Request 1 → [Server handles] → Response
4. Request 2 → [Same server handles] → Response  
5. Request 3 → [Same server handles] → Response
6. Timeout (5 hours) → Container destroyed
```
- YOU manage container lifecycle
- Container is stateful (server maintains state)
- Manual scaling (create more sandboxes yourself)

### So, Is Sandbox "Longer Lambdas"?

**No, because:**

| Aspect | Lambda | Sandbox |
|--------|--------|---------|
| **Invocation** | Automatic (per request) | Manual (you create) |
| **State** | Stateless | Stateful |
| **Scaling** | Automatic | Manual |
| **Lifecycle** | Lambda manages | You manage |
| **Model** | Event-driven function | Persistent container |
| **Use Case** | API endpoints, event processing | Code execution, temporary servers |

### Better Analogy

**Lambda** = Taxi service (call when needed, driver picks you up, drops you off, becomes available)

**Sandbox** = Rented car (you rent it, drive it around, it's yours until rental period ends, you manage refueling/maintenance)

**Fargate** = Owning a car (always available, you maintain it, runs indefinitely)

### For Your Use Case

Your FastAPI SSE endpoint needs:
- ✅ Persistent HTTP server (like Fargate or Sandbox)
- ✅ Long-running connections (Lambda can't do this well)
- ✅ Automatic scaling (Fargate ✅, Sandbox ❌)
- ✅ No time limits (Fargate ✅, Sandbox has 5-hour limit ❌)

**Conclusion:** Sandbox is more like "temporary Fargate" than "longer Lambda" - it's a persistent container, not a stateless function.

