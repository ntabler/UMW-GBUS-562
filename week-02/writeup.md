## What I Built

The prompt lab utilizes Anthropic API key to call Claude with a prompt, and deliver a response to the user through a UI. I used this setup to execute four prompts that solve business problems for the fictional specialty coffee roaster called Wildframe Coffee Roasters.

## Why This Approach

Task 1 - Technique: zero-shot
Task one fits the zero-shot model because it's an open ended request, requiring idea generations, with little or no examples to use as reference. For this task, examples may have narrowed down the variety in response.

Task 2 - Technique: one-shot
Task two fits the one-shot model because it requires a specific structure that the business requires. Since the specific structure is used for each response, adding more examples would not be helpful.

Task 3 - Technique: few-shot
Task three fits the few-shot model because it requires matching a tone of voice that could require a few examples to nail down. Providing only one example may not be enough for an accurate response from Claude.

Task 4 - Technique: chain-of-thought
Task four fits the chain-of-thought model because it allows the business to see step by step what calculations were made to come to the overall conclusion. This can be fact-checked to ensure accuracy and optimize if necessary.

## Example Run

Full export of runs/task-4.md:

```markdown
claude-sonnet-5

### You

You are a marketing analyst for Wildframe Coffee Roasters.

Wildframe Coffee Roasters is a specialty coffee roaster that sells beans online and through a monthly subscription. Customers are environmentally conscious urban professionals.

Wildframe can run one four-week pop-up stand this spring, with a total rent budget of $5,000 for the four weeks. Work through this step by step before recommending where to open next popup. 
The response must end with a single line that starts with Recommendation: followed by the location's name.

Riverside Farmers Market: 9,000 visitors per week; 2% of visitors buy; average sale $14 (mostly whole-bean bags); rent $900 per week.
Union Station concourse: 30,000 visitors per week; 0.5% of visitors buy; average sale $6 (mostly single cups); rent $2,000 per week.
Campus library atrium: 6,000 visitors per week; 3% of visitors buy; average sale $5 (single cups); rent $600 per week.

### Claude

# Step-by-Step Analysis

## Step 1: Calculate Weekly Buyers and Revenue

**Riverside Farmers Market**
- Buyers: 9,000 × 2% = 180 buyers/week
- Revenue: 180 × $14 = **$2,520/week**

**Union Station Concourse**
- Buyers: 30,000 × 0.5% = 150 buyers/week
- Revenue: 150 × $6 = **$900/week**

**Campus Library Atrium**
- Buyers: 6,000 × 3% = 180 buyers/week
- Revenue: 180 × $5 = **$900/week**

## Step 2: Calculate Weekly Profit (Revenue − Rent)

| Location | Revenue | Rent | Weekly Profit |
|---|---|---|---|
| Riverside | $2,520 | $900 | **+$1,620** |
| Union Station | $900 | $2,000 | **–$1,100** |
| Campus Library | $900 | $600 | **+$300** |

Union Station immediately stands out as a problem — it loses money every single week before even considering inventory, staffing, or other costs.

## Step 3: Check Against the 4-Week, $5,000 Total Budget

| Location | 4-Week Rent | Fits Budget? | 4-Week Revenue | 4-Week Profit |
|---|---|---|---|---|
| Riverside | $3,600 | ✅ Yes ($1,400 to spare) | $10,080 | **$6,480** |
| Union Station | $8,000 | ❌ No — exceeds $5,000 | $3,600 | –$4,400 |
| Campus Library | $2,400 | ✅ Yes ($2,600 to spare) | $3,600 | $1,200 |

Union Station is eliminated on two counts: it's unaffordable within the budget, and it's unprofitable even if the budget allowed it.

## Step 4: Compare the Two Viable Options

- **Riverside Farmers Market**: $6,480 profit over 4 weeks, uses 72% of budget
- **Campus Library Atrium**: $1,200 profit over 4 weeks, uses 48% of budget

Riverside generates **5.4x more profit** than Campus Library, even after paying higher rent. The combination of a meaningful buy rate (2%) *and* a high-margin product (whole-bean bags at $14 vs. single cups at $5) makes it far more lucrative than either alternative.

## Step 5: Strategic Fit Check

Riverside Farmers Market also aligns well with Wildframe's target customer (environmentally conscious urban professionals), who are likely to frequent farmers markets valuing quality, sustainability, and artisanal products — reinforcing the brand positioning beyond just the financial case.

## Conclusion

Riverside Farmers Market delivers the highest weekly and total profit, stays comfortably within the $5,000 budget, and matches Wildframe's target demographic and product strengths (whole-bean bags).

Recommendation: Riverside Farmers Market
```

## Reflection

While reviewing each task's Claude response, a few noteworthy takeaways come to mind. Task four utilizes the chain of thought technique which provides the most detail and confidence in response accuracy. For this reason, I thought task 4 was the strongest response out of all the tasks and made the biggest difference when comparing techniques. Task three had a response that made up facts ("Swiss Water process," "Sumatra decaf") that weren't in the prompt. Inventing facts is a real risk with customer replies. Because of this, I was most unsure of task 3's response. In the future I would probably ensure the prompts were not adding imaginary process or products to the responses and attempt to rerun the prompt given for Task three.