#!/usr/bin/env python3
"""
Test script for Orchestrator API
"""

import asyncio
import json
import sys

import httpx


async def test_health():
    """Test health endpoint"""
    print("Testing /health endpoint...")
    async with httpx.AsyncClient(verify=False) as client:
        response = await client.get("http://localhost:8080/health")
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}")
        print()


async def test_steering():
    """Test steering endpoint"""
    print("Testing /steering endpoint...")
    async with httpx.AsyncClient(verify=False) as client:
        response = await client.get("http://localhost:8080/steering")
        print(f"Status: {response.status_code}")
        data = response.json()
        print(f"Steering files: {data['steering_files']}")
        print(f"Standard labels: {data['standard_labels']}")
        print()


async def test_investigate_alert():
    """Test investigate endpoint with alert UID"""
    print("Testing /investigate endpoint (ALERT_UID)...")
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        payload = {
            "input_type": "ALERT_UID",
            "value": "abc123def456",
            "user": "test@example.com"
        }
        
        try:
            response = await client.post(
                "http://localhost:8080/investigate",
                json=payload
            )
            print(f"Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print(f"CaseFile ID: {data['caseFileId']}")
                print(f"Scope: {data['scope']}")
                print(f"Time Window: {data['timeWindow']}")
                print(f"Evidence count: {len(data['evidence'])}")
                print(f"Hypotheses count: {len(data['hypotheses'])}")
                print(f"Correlation gaps: {len(data['correlationGaps'])}")
                print(f"Execution time: {data['executionTime']}s")
                
                if data['hypotheses']:
                    print("\nTop Hypothesis:")
                    h = data['hypotheses'][0]
                    print(f"  Component: {h['suspectedComponent']}")
                    print(f"  Root Cause: {h['rootCause']}")
                    print(f"  Confidence: {h['confidence']}")
                    print(f"  Next Steps: {len(h['nextSteps'])}")
            else:
                print(f"Error: {response.text}")
        
        except Exception as e:
            print(f"Error: {e}")
        
        print()


async def test_investigate_symptom():
    """Test investigate endpoint with symptom"""
    print("Testing /investigate endpoint (SYMPTOM)...")
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        payload = {
            "input_type": "SYMPTOM",
            "value": "API Gateway returning 500 errors in production",
            "user": "test@example.com"
        }
        
        try:
            response = await client.post(
                "http://localhost:8080/investigate",
                json=payload
            )
            print(f"Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print(f"CaseFile ID: {data['caseFileId']}")
                print(f"Scope: {data['scope']}")
                print(f"Execution time: {data['executionTime']}s")
            else:
                print(f"Error: {response.text}")
        
        except Exception as e:
            print(f"Error: {e}")
        
        print()


async def main():
    """Run all tests"""
    print("=" * 60)
    print("Orchestrator API Tests")
    print("=" * 60)
    print()
    
    try:
        await test_health()
        await test_steering()
        await test_investigate_alert()
        await test_investigate_symptom()
        
        print("=" * 60)
        print("All tests completed!")
        print("=" * 60)
    
    except httpx.ConnectError:
        print("ERROR: Cannot connect to orchestrator at http://localhost:8080")
        print("Make sure orchestrator is running:")
        print("  python orchestrator.py")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
