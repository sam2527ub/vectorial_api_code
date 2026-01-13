"""
Alternative script to export prompts in a format that can be easily
copied and pasted into LangSmith UI if the upload script fails due to
Pydantic version issues.

This script will print all prompts in a format ready for manual upload.
"""
from upload_summary_prompts_safe import NEW_PROMPTS

print("=" * 80)
print("📋 PROMPTS READY FOR MANUAL UPLOAD TO LANGSMITH UI")
print("=" * 80)
print()
print("If the upload script fails due to Pydantic version issues,")
print("you can manually upload these prompts via the LangSmith UI.")
print()
print("=" * 80)
print()

for prompt_name, prompt_template in NEW_PROMPTS.items():
    print(f"\n{'='*80}")
    print(f"PROMPT NAME: {prompt_name}")
    print(f"{'='*80}")
    print("\nPROMPT CONTENT:")
    print("-" * 80)
    print(prompt_template)
    print("-" * 80)
    print(f"\nVariables used: {', '.join(set(__import__('re').findall(r'\{(\w+)\}', prompt_template)))}")
    print()

print("=" * 80)
print("✅ All prompts exported!")
print("=" * 80)
print("\nTo upload manually:")
print("1. Go to LangSmith UI")
print("2. Navigate to Prompts section")
print("3. Click 'Create Prompt'")
print("4. Copy and paste each prompt above")
print("=" * 80)

