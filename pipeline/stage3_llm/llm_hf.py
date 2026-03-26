#!/usr/bin/env python3
"""
Stage 3: LLM-Assisted Scenario Generation (Template-Based)
============================================================
Instead of generating full XML (fragile), the LLM only fills in
scenario parameters (speeds, positions, trigger values) into
validated templates taken from Stage 2's working scenarios.

This guarantees structural validity while still using the LLM
for intelligent parameter selection — which is the actual
research contribution.
"""
import os
import json
import time
import copy
import random
import requests
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime


# ── Scenario templates (structurally valid, taken from Stage 2 patterns) ─────

TEMPLATES = {
    "following_distance": '''<?xml version="1.0"?>
<OpenSCENARIO>
  <FileHeader revMajor="1" revMinor="0" description="{description}"/>
  <ParameterDeclarations/>
  <CatalogLocations/>
  <RoadNetwork>
    <LogicFile filepath="Town01"/>
  </RoadNetwork>
  <Entities>
    <ScenarioObject name="hero">
      <Vehicle name="vehicle.lincoln.mkz_2017" vehicleCategory="car">
        <ParameterDeclarations/>
        <Performance maxSpeed="69.444" maxAcceleration="200" maxDeceleration="10.0"/>
        <BoundingBox><Center x="1.5" y="0.0" z="0.9"/><Dimensions width="2.1" length="4.5" height="1.8"/></BoundingBox>
        <Axles>
          <FrontAxle maxSteering="0.5" wheelDiameter="0.6" trackWidth="1.8" positionX="3.1" positionZ="0.3"/>
          <RearAxle  maxSteering="0.0" wheelDiameter="0.6" trackWidth="1.8" positionX="0.0" positionZ="0.3"/>
        </Axles>
        <Properties>
          <Property name="type" value="ego_vehicle"/>
          <Property name="rolename" value="hero"/>
        </Properties>
      </Vehicle>
    </ScenarioObject>
    <ScenarioObject name="adversary">
      <Vehicle name="vehicle.tesla.model3" vehicleCategory="car">
        <ParameterDeclarations/>
        <Performance maxSpeed="69.444" maxAcceleration="200" maxDeceleration="10.0"/>
        <BoundingBox><Center x="1.5" y="0.0" z="0.9"/><Dimensions width="2.1" length="4.5" height="1.8"/></BoundingBox>
        <Axles>
          <FrontAxle maxSteering="0.5" wheelDiameter="0.6" trackWidth="1.8" positionX="3.1" positionZ="0.3"/>
          <RearAxle  maxSteering="0.0" wheelDiameter="0.6" trackWidth="1.8" positionX="0.0" positionZ="0.3"/>
        </Axles>
        <Properties><Property name="type" value="simulation"/></Properties>
      </Vehicle>
    </ScenarioObject>
  </Entities>
  <Storyboard>
    <Init>
      <Actions>
        <Private entityRef="hero">
          <PrivateAction>
            <TeleportAction><Position>
              <WorldPosition x="{hero_x}" y="{hero_y}" z="0.3" h="0"/>
            </Position></TeleportAction>
          </PrivateAction>
        </Private>
        <Private entityRef="adversary">
          <PrivateAction>
            <TeleportAction><Position>
              <WorldPosition x="{adv_x}" y="{adv_y}" z="0.3" h="0"/>
            </Position></TeleportAction>
          </PrivateAction>
        </Private>
      </Actions>
    </Init>
    <Story name="FollowingStory">
      <Act name="FollowingAct">
        <ManeuverGroup name="AdversaryGroup" maximumExecutionCount="1">
          <Actors selectTriggeringEntities="false">
            <EntityRef entityRef="adversary"/>
          </Actors>
          <Maneuver name="AdversaryManeuver">
            <Event name="BrakeEvent" priority="overwrite">
              <Action name="BrakeAction">
                <PrivateAction>
                  <LongitudinalAction>
                    <SpeedAction>
                      <SpeedActionDynamics dynamicsShape="linear" value="{brake_rate}" dynamicsDimension="rate"/>
                      <SpeedActionTarget>
                        <AbsoluteTargetSpeed value="{adv_target_speed}"/>
                      </SpeedActionTarget>
                    </SpeedAction>
                  </LongitudinalAction>
                </PrivateAction>
              </Action>
              <StartTrigger>
                <ConditionGroup>
                  <Condition name="BrakeTrigger" delay="0" conditionEdge="rising">
                    <ByValueCondition>
                      <SimulationTimeCondition value="{trigger_time}" rule="greaterThan"/>
                    </ByValueCondition>
                  </Condition>
                </ConditionGroup>
              </StartTrigger>
            </Event>
          </Maneuver>
        </ManeuverGroup>
        <StartTrigger>
          <ConditionGroup>
            <Condition name="ActStart" delay="0" conditionEdge="rising">
              <ByValueCondition>
                <SimulationTimeCondition value="0" rule="greaterThan"/>
              </ByValueCondition>
            </Condition>
          </ConditionGroup>
        </StartTrigger>
      </Act>
    </Story>
    <StopTrigger>
    <ConditionGroup>
      <Condition name="EndCondition" delay="0" conditionEdge="rising">
        <ByValueCondition>
          <SimulationTimeCondition value="{duration}" rule="greaterThan"/>
        </ByValueCondition>
      </Condition>
    </ConditionGroup>
  </StopTrigger>
  </Storyboard>
</OpenSCENARIO>''',

    "pedestrian_crossing": '''<?xml version="1.0"?>
<OpenSCENARIO>
  <FileHeader revMajor="1" revMinor="0" description="{description}"/>
  <ParameterDeclarations/>
  <CatalogLocations/>
  <RoadNetwork>
    <LogicFile filepath="Town01"/>
  </RoadNetwork>
  <Entities>
    <ScenarioObject name="hero">
      <Vehicle name="vehicle.lincoln.mkz_2017" vehicleCategory="car">
        <ParameterDeclarations/>
        <Performance maxSpeed="69.444" maxAcceleration="200" maxDeceleration="10.0"/>
        <BoundingBox><Center x="1.5" y="0.0" z="0.9"/><Dimensions width="2.1" length="4.5" height="1.8"/></BoundingBox>
        <Axles>
          <FrontAxle maxSteering="0.5" wheelDiameter="0.6" trackWidth="1.8" positionX="3.1" positionZ="0.3"/>
          <RearAxle  maxSteering="0.0" wheelDiameter="0.6" trackWidth="1.8" positionX="0.0" positionZ="0.3"/>
        </Axles>
        <Properties>
          <Property name="type" value="ego_vehicle"/>
          <Property name="rolename" value="hero"/>
        </Properties>
      </Vehicle>
    </ScenarioObject>
    <ScenarioObject name="pedestrian">
      <Pedestrian model="walker.pedestrian.0001" mass="80" name="walker" pedestrianCategory="pedestrian">
        <ParameterDeclarations/>
        <BoundingBox><Center x="0.5" y="0.0" z="0.9"/><Dimensions width="0.5" length="0.5" height="1.8"/></BoundingBox>
        <Properties/>
      </Pedestrian>
    </ScenarioObject>
  </Entities>
  <Storyboard>
    <Init>
      <Actions>
        <Private entityRef="hero">
          <PrivateAction>
            <TeleportAction><Position>
              <WorldPosition x="{hero_x}" y="{hero_y}" z="0.3" h="0"/>
            </Position></TeleportAction>
          </PrivateAction>
        </Private>
        <Private entityRef="pedestrian">
          <PrivateAction>
            <TeleportAction><Position>
              <WorldPosition x="{ped_x}" y="{ped_y}" z="0.3" h="1.57"/>
            </Position></TeleportAction>
          </PrivateAction>
        </Private>
      </Actions>
    </Init>
    <Story name="PedestrianStory">
      <Act name="PedestrianAct">
        <ManeuverGroup name="PedestrianGroup" maximumExecutionCount="1">
          <Actors selectTriggeringEntities="false">
            <EntityRef entityRef="pedestrian"/>
          </Actors>
          <Maneuver name="CrossingManeuver">
            <Event name="WalkEvent" priority="overwrite">
              <Action name="WalkAction">
                <PrivateAction>
                  <LongitudinalAction>
                    <SpeedAction>
                      <SpeedActionDynamics dynamicsShape="step" value="1.0" dynamicsDimension="time"/>
                      <SpeedActionTarget>
                        <AbsoluteTargetSpeed value="{ped_speed}"/>
                      </SpeedActionTarget>
                    </SpeedAction>
                  </LongitudinalAction>
                </PrivateAction>
              </Action>
              <StartTrigger>
                <ConditionGroup>
                  <Condition name="WalkTrigger" delay="0" conditionEdge="rising">
                    <ByValueCondition>
                      <SimulationTimeCondition value="{trigger_time}" rule="greaterThan"/>
                    </ByValueCondition>
                  </Condition>
                </ConditionGroup>
              </StartTrigger>
            </Event>
          </Maneuver>
        </ManeuverGroup>
        <StartTrigger>
          <ConditionGroup>
            <Condition name="ActStart" delay="0" conditionEdge="rising">
              <ByValueCondition>
                <SimulationTimeCondition value="0" rule="greaterThan"/>
              </ByValueCondition>
            </Condition>
          </ConditionGroup>
        </StartTrigger>
      </Act>
    </Story>
    <StopTrigger>
    <ConditionGroup>
      <Condition name="EndCondition" delay="0" conditionEdge="rising">
        <ByValueCondition>
          <SimulationTimeCondition value="{duration}" rule="greaterThan"/>
        </ByValueCondition>
      </Condition>
    </ConditionGroup>
  </StopTrigger>
  </Storyboard>
</OpenSCENARIO>''',

    "speed_limit": '''<?xml version="1.0"?>
<OpenSCENARIO>
  <FileHeader revMajor="1" revMinor="0" description="{description}"/>
  <ParameterDeclarations/>
  <CatalogLocations/>
  <RoadNetwork>
    <LogicFile filepath="Town01"/>
  </RoadNetwork>
  <Entities>
    <ScenarioObject name="hero">
      <Vehicle name="vehicle.lincoln.mkz_2017" vehicleCategory="car">
        <ParameterDeclarations/>
        <Performance maxSpeed="69.444" maxAcceleration="200" maxDeceleration="10.0"/>
        <BoundingBox><Center x="1.5" y="0.0" z="0.9"/><Dimensions width="2.1" length="4.5" height="1.8"/></BoundingBox>
        <Axles>
          <FrontAxle maxSteering="0.5" wheelDiameter="0.6" trackWidth="1.8" positionX="3.1" positionZ="0.3"/>
          <RearAxle  maxSteering="0.0" wheelDiameter="0.6" trackWidth="1.8" positionX="0.0" positionZ="0.3"/>
        </Axles>
        <Properties>
          <Property name="type" value="ego_vehicle"/>
          <Property name="rolename" value="hero"/>
        </Properties>
      </Vehicle>
    </ScenarioObject>
  </Entities>
  <Storyboard>
    <Init>
      <Actions>
        <Private entityRef="hero">
          <PrivateAction>
            <TeleportAction><Position>
              <WorldPosition x="{hero_x}" y="{hero_y}" z="0.3" h="0"/>
            </Position></TeleportAction>
          </PrivateAction>
        </Private>
      </Actions>
    </Init>
    <Story name="SpeedStory">
      <Act name="SpeedAct">
        <ManeuverGroup name="HeroGroup" maximumExecutionCount="1">
          <Actors selectTriggeringEntities="false">
            <EntityRef entityRef="hero"/>
          </Actors>
          <Maneuver name="SpeedManeuver">
            <Event name="SpeedEvent" priority="overwrite">
              <Action name="SpeedAction">
                <PrivateAction>
                  <LongitudinalAction>
                    <SpeedAction>
                      <SpeedActionDynamics dynamicsShape="linear" value="{accel_rate}" dynamicsDimension="rate"/>
                      <SpeedActionTarget>
                        <AbsoluteTargetSpeed value="{target_speed}"/>
                      </SpeedActionTarget>
                    </SpeedAction>
                  </LongitudinalAction>
                </PrivateAction>
              </Action>
              <StartTrigger>
                <ConditionGroup>
                  <Condition name="SpeedTrigger" delay="0" conditionEdge="rising">
                    <ByValueCondition>
                      <SimulationTimeCondition value="{trigger_time}" rule="greaterThan"/>
                    </ByValueCondition>
                  </Condition>
                </ConditionGroup>
              </StartTrigger>
            </Event>
          </Maneuver>
        </ManeuverGroup>
        <StartTrigger>
          <ConditionGroup>
            <Condition name="ActStart" delay="0" conditionEdge="rising">
              <ByValueCondition>
                <SimulationTimeCondition value="0" rule="greaterThan"/>
              </ByValueCondition>
            </Condition>
          </ConditionGroup>
        </StartTrigger>
      </Act>
    </Story>
    <StopTrigger>
    <ConditionGroup>
      <Condition name="EndCondition" delay="0" conditionEdge="rising">
        <ByValueCondition>
          <SimulationTimeCondition value="{duration}" rule="greaterThan"/>
        </ByValueCondition>
      </Condition>
    </ConditionGroup>
  </StopTrigger>
  </Storyboard>
</OpenSCENARIO>'''
}

# ── Map requirements to templates ────────────────────────────────────────────

REQUIREMENT_TEMPLATE_MAP = {
    "Vehicle must maintain safe following distance of at least 2 seconds": "following_distance",
    "Vehicle must stop at red traffic lights":                              "speed_limit",
    "Vehicle must yield to pedestrians at crosswalks":                     "pedestrian_crossing",
    "Vehicle must not exceed urban speed limit of 50 km/h":                "speed_limit",
    "Vehicle must avoid collision with stopped vehicles":                  "following_distance",
    "Vehicle must handle sudden pedestrian crossing":                      "pedestrian_crossing",
    "Vehicle must merge safely into traffic":                               "following_distance",
    "Vehicle must navigate around obstacles":                               "following_distance",
}


# ── LLM client (unchanged from working version) ───────────────────────────────

def load_token_from_env_file():
    env_file = Path("/home/zaeem/Desktop/carla-cicd-pipeline/.env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith('HF_TOKEN='):
                token = line.split('=', 1)[1].strip().strip("'").strip('"')
                print(f"✓ Token loaded from {env_file}")
                return token
    token = os.getenv('HF_TOKEN')
    if not token:
        print(f"✗ ERROR: HF_TOKEN not found!")
    return token


class RateLimiter:
    def __init__(self, calls_per_minute=8):
        self.min_interval = 60.0 / calls_per_minute
        self.last_called = 0.0

    def wait(self):
        elapsed = time.time() - self.last_called
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_called = time.time()


class LLMClient:
    def __init__(self, token: str = None):
        self.token = (token or load_token_from_env_file()).strip()
        self.api_url = "https://api.groq.com/openai/v1/chat/completions"
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        self.rate_limiter = RateLimiter(calls_per_minute=8)

    def generate(self, prompt: str, max_retries: int = 3) -> Optional[str]:
        for attempt in range(max_retries):
            try:
                self.rate_limiter.wait()
                payload = {
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are an autonomous driving test engineer. "
                                "When asked for JSON parameters, respond ONLY with valid JSON. "
                                "No explanation, no markdown, no code fences."
                            )
                        },
                        {"role": "user", "content": prompt}
                    ],
                    "max_tokens": 512,
                    "temperature": 0.7,
                }
                response = requests.post(
                    self.api_url, headers=self.headers, json=payload, timeout=60
                )
                if response.status_code == 200:
                    return response.json()['choices'][0]['message']['content'].strip()
                elif response.status_code in (429, 503):
                    time.sleep(60 * (attempt + 1))
                else:
                    print(f"  API error {response.status_code}: {response.text[:200]}")
                    time.sleep(5 * (attempt + 1))
            except Exception as e:
                print(f"  Attempt {attempt + 1} failed: {e}")
                time.sleep(5 * (attempt + 1))
        return None


# ── Parameter generator ───────────────────────────────────────────────────────

class ParameterGenerator:
    """
    Asks the LLM to generate only the numeric parameters for a scenario.
    Falls back to random valid values if the LLM fails or returns bad JSON.
    """

    PARAM_PROMPTS = {
        "following_distance": """Generate JSON parameters for a CARLA following-distance scenario.
Safety requirement: "{requirement}"

Return ONLY this JSON with numeric values filled in:
{{
  "description": "short scenario description",
  "hero_x": <float 100-200>,
  "hero_y": <float 100-200>,
  "adv_x": <float, hero_x - 10 to 30>,
  "adv_y": <same as hero_y>,
  "adv_target_speed": <float 0-5, adversary brakes to this m/s>,
  "brake_rate": <float 1-8, deceleration rate m/s²>,
  "trigger_time": <float 2-5, seconds before adversary brakes>,
  "duration": <float 20-40>
}}""",

        "pedestrian_crossing": """Generate JSON parameters for a CARLA pedestrian crossing scenario.
Safety requirement: "{requirement}"

Return ONLY this JSON with numeric values filled in:
{{
  "description": "short scenario description",
  "hero_x": <float 100-200>,
  "hero_y": <float 100-200>,
  "ped_x": <float, hero_x + 15 to 30>,
  "ped_y": <float, hero_y - 3 to 3>,
  "ped_speed": <float 0.8-2.0, pedestrian walking speed m/s>,
  "trigger_time": <float 1-4>,
  "duration": <float 20-35>
}}""",

        "speed_limit": """Generate JSON parameters for a CARLA speed limit test scenario.
Safety requirement: "{requirement}"

Return ONLY this JSON with numeric values filled in:
{{
  "description": "short scenario description",
  "hero_x": <float 100-200>,
  "hero_y": <float 100-200>,
  "target_speed": <float 8-20, ego target speed m/s>,
  "accel_rate": <float 1-4, acceleration rate m/s²>,
  "trigger_time": <float 1-3>,
  "duration": <float 20-35>
}}"""
    }

    FALLBACK_PARAMS = {
        "following_distance": lambda: {
            "description": "Following distance safety test",
            "hero_x": round(random.uniform(150, 190), 1),
            "hero_y": round(random.uniform(150, 190), 1),
            "adv_x": round(random.uniform(120, 145), 1),
            "adv_y": round(random.uniform(150, 190), 1),
            "adv_target_speed": round(random.uniform(0, 3), 1),
            "brake_rate": round(random.uniform(2, 6), 1),
            "trigger_time": round(random.uniform(2, 4), 1),
            "duration": round(random.uniform(25, 35), 0),
        },
        "pedestrian_crossing": lambda: {
            "description": "Pedestrian crossing safety test",
            "hero_x": round(random.uniform(150, 180), 1),
            "hero_y": round(random.uniform(150, 180), 1),
            "ped_x": round(random.uniform(165, 200), 1),
            "ped_y": round(random.uniform(150, 180), 1),
            "ped_speed": round(random.uniform(1.0, 1.8), 1),
            "trigger_time": round(random.uniform(1, 3), 1),
            "duration": round(random.uniform(20, 30), 0),
        },
        "speed_limit": lambda: {
            "description": "Speed limit compliance test",
            "hero_x": round(random.uniform(150, 190), 1),
            "hero_y": round(random.uniform(150, 190), 1),
            "target_speed": round(random.uniform(10, 18), 1),
            "accel_rate": round(random.uniform(1.5, 3.5), 1),
            "trigger_time": round(random.uniform(1, 3), 1),
            "duration": round(random.uniform(20, 30), 0),
        },
    }

    def __init__(self, llm_client: LLMClient):
        self.client = llm_client

    def get_params(self, template_name: str, requirement: str) -> dict:
        """Get parameters from LLM, fall back to random if LLM fails."""
        prompt = self.PARAM_PROMPTS[template_name].format(requirement=requirement)
        raw = self.client.generate(prompt)

        if raw:
            try:
                # Strip any accidental markdown
                clean = raw.replace('```json', '').replace('```', '').strip()
                params = json.loads(clean)
                print(f"  ✓ LLM parameters: {params}")
                return params
            except json.JSONDecodeError as e:
                print(f"  ⚠ LLM returned invalid JSON ({e}), using fallback")

        # Fallback: random valid parameters
        params = self.FALLBACK_PARAMS[template_name]()
        print(f"  ℹ Fallback parameters: {params}")
        return params


# ── Main generator ────────────────────────────────────────────────────────────

class ScenarioGenerator:
    def __init__(self, output_dir: Path = Path("./generated_scenarios")):
        self.client = LLMClient()
        self.param_gen = ParameterGenerator(self.client)
        self.output_dir = output_dir
        self.output_dir.mkdir(exist_ok=True)

        self.safety_requirements = [
            "Vehicle must maintain safe following distance of at least 2 seconds",
            "Vehicle must stop at red traffic lights",
            "Vehicle must yield to pedestrians at crosswalks",
            "Vehicle must not exceed urban speed limit of 50 km/h",
            "Vehicle must avoid collision with stopped vehicles",
            "Vehicle must handle sudden pedestrian crossing",
            "Vehicle must merge safely into traffic",
            "Vehicle must navigate around obstacles",
        ]

    def generate_scenario(self, requirement: str, agent_type: str) -> Optional[str]:
        """Fill template with LLM-generated parameters."""
        template_name = REQUIREMENT_TEMPLATE_MAP.get(requirement, "following_distance")
        template = TEMPLATES[template_name]

        params = self.param_gen.get_params(template_name, requirement)

        try:
            scenario_xml = template.format(**params)
            # Quick sanity check
            ET.fromstring(scenario_xml)
            return scenario_xml
        except KeyError as e:
            print(f"  ✗ Template parameter missing: {e}")
            # Retry with fallback params
            from pipeline.stage3_llm.llm_hf import ParameterGenerator
            params = ParameterGenerator.FALLBACK_PARAMS[template_name]()
            try:
                return template.format(**params)
            except Exception:
                return None
        except ET.ParseError as e:
            print(f"  ✗ XML error after substitution: {e}")
            return None

    def generate_for_project(self, project_name: str, agent_type: str,
                              num_scenarios: int = 20) -> dict:
        print(f"\n{'='*60}")
        print(f"Generating scenarios for: {project_name}")
        print(f"Agent type: {agent_type}")
        print(f"Target: {num_scenarios} scenarios")
        print(f"Method: template + LLM parameters")
        print(f"{'='*60}\n")

        results = {
            'project': project_name,
            'agent_type': agent_type,
            'timestamp': datetime.now().isoformat(),
            'scenarios': [],
            'stats': {'attempted': 0, 'successful': 0, 'failed': 0}
        }

        scenario_count = 0
        req_idx = 0

        while scenario_count < num_scenarios:
            requirement = self.safety_requirements[req_idx % len(self.safety_requirements)]
            req_idx += 1

            print(f"[{scenario_count + 1}/{num_scenarios}] {requirement}")
            results['stats']['attempted'] += 1

            scenario_xml = self.generate_scenario(requirement, agent_type)

            if scenario_xml:
                filename = f"{project_name}_scenario_{scenario_count + 1:03d}.xosc"
                filepath = self.output_dir / filename
                filepath.write_text(scenario_xml)

                results['scenarios'].append({
                    'filename': filename,
                    'requirement': requirement,
                    'filepath': str(filepath)
                })
                results['stats']['successful'] += 1
                scenario_count += 1
                print(f"  ✓ Saved: {filename}\n")
            else:
                results['stats']['failed'] += 1
                print()

        metadata_file = self.output_dir / f"{project_name}_metadata.json"
        metadata_file.write_text(json.dumps(results, indent=2))

        print(f"\n{'='*60}")
        print(f"Complete: {results['stats']['successful']}/{results['stats']['attempted']} scenarios")
        print(f"Saved to: {self.output_dir}")
        print(f"{'='*60}\n")

        return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Stage 3: LLM Scenario Generation')
    parser.add_argument('--project', required=True)
    parser.add_argument('--agent-type', choices=['perception', 'e2e', 'planning'],
                        default='perception')
    parser.add_argument('--num-scenarios', type=int, default=20)
    parser.add_argument('--output-dir', type=Path, default=Path('./generated_scenarios'))
    args = parser.parse_args()

    token = load_token_from_env_file()
    if not token:
        print("ERROR: HF_TOKEN not set")
        return 1
    print("Hugging Face token found ✓")

    generator = ScenarioGenerator(output_dir=args.output_dir)
    generator.generate_for_project(
        project_name=args.project,
        agent_type=args.agent_type,
        num_scenarios=args.num_scenarios
    )
    return 0


if __name__ == "__main__":
    exit(main())