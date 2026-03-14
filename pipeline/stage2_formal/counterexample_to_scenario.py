"""
Counterexample to OpenSCENARIO Converter
==========================================
Takes nuXmv counterexample traces and translates each one into
an OpenSCENARIO (.xosc) file that CARLA ScenarioRunner can execute.

Each counterexample is a sequence of states that violates a safety property.
We translate that state sequence into concrete actor positions, speeds,
and trigger conditions that recreate the hazardous situation in CARLA.
"""

import json
import os
import argparse
from dataclasses import dataclass
from typing import List, Dict, Optional
from pathlib import Path


# CARLA Town to use for generated scenarios
DEFAULT_TOWN = "Town03"

# Actor blueprint names
EGO_BLUEPRINT    = "vehicle.tesla.model3"
NPC_BLUEPRINT    = "vehicle.audi.a2"
PEDESTRIAN_BLUEPRINT = "walker.pedestrian.0001"


@dataclass
class ScenarioConfig:
    """Configuration extracted from a counterexample for one scenario."""
    name: str
    description: str
    property_violated: str
    town: str
    ego_speed: float          # km/h
    npc_speed: float          # km/h
    following_distance: float # metres
    has_pedestrian: bool
    traffic_light_red: bool
    trigger_distance: float   # metres — when NPC triggers action
    steps: List[Dict]         # raw counterexample steps


def extract_scenario_config(ce: dict, index: int) -> ScenarioConfig:
    """
    Extract a concrete scenario configuration from a counterexample trace.
    Uses the first state that shows the violation as the trigger point.
    """
    prop_name = ce.get('property_name', f'property_{index}')
    steps     = ce.get('steps', [])

    # Find the most "dangerous" state in the trace
    # (the one closest to the violation)
    danger_step = steps[-1] if steps else {}
    vars = danger_step.get('variables', {}) if danger_step else {}

    # Extract numeric values with safe defaults
    def get_int(key, default):
        val = vars.get(key, str(default))
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def get_bool(key):
        return vars.get(key, 'FALSE').upper() == 'TRUE'

    ego_speed         = float(get_int('vehicle_speed', 30))
    following_dist    = float(get_int('following_distance', 10))
    has_pedestrian    = get_bool('pedestrian_in_path')
    traffic_light_red = vars.get('traffic_light_state', 'green') == 'red'

    # NPC moves slightly faster to create closing scenario
    npc_speed = max(0.0, ego_speed - 10.0)

    description_map = {
        'ttc_threshold':           'TTC drops below 1500ms — NPC brakes suddenly ahead of ego',
        'urban_speed_limit':       'Ego exceeds urban speed limit — NPC triggers at intersection',
        'red_light_compliance':    'Ego fails to stop at red light — NPC crosses intersection',
        'no_collision':            'Collision scenario — vehicles on collision course',
        'safe_following_distance': 'Following distance drops below minimum — rear-end risk',
        'pedestrian_safety':       'Pedestrian steps into path — ego fails to brake',
        'agent_moves':             'Agent becomes stuck — no forward progress achieved',
    }

    return ScenarioConfig(
        name=f"CE_{index:03d}_{prop_name}",
        description=description_map.get(prop_name, f"Counterexample for {prop_name}"),
        property_violated=prop_name,
        town=DEFAULT_TOWN,
        ego_speed=ego_speed,
        npc_speed=npc_speed,
        following_distance=following_dist,
        has_pedestrian=has_pedestrian,
        traffic_light_red=traffic_light_red,
        trigger_distance=max(following_dist, 15.0),
        steps=steps,
    )


def generate_xosc(cfg: ScenarioConfig) -> str:
    """
    Generate an OpenSCENARIO 1.0 XML file from a scenario config.
    This is the format CARLA ScenarioRunner expects.
    """

    pedestrian_block = ""
    if cfg.has_pedestrian:
        pedestrian_block = f"""
        <!-- Pedestrian entity from counterexample -->
        <EntityObject name="pedestrian_0">
          <MiscObject miscObjectType="pedestrian" mass="80.0" name="pedestrian_0">
            <BoundingBox>
              <Center x="0" y="0" z="0.9"/>
              <Dimensions width="0.5" length="0.5" height="1.8"/>
            </BoundingBox>
            <Properties/>
          </MiscObject>
        </EntityObject>"""

    pedestrian_init = ""
    if cfg.has_pedestrian:
        pedestrian_init = f"""
            <Private entityRef="pedestrian_0">
              <PrivateAction>
                <TeleportAction>
                  <Position>
                    <RoadPosition roadId="0" s="20.0" t="2.5"/>
                  </Position>
                </TeleportAction>
              </PrivateAction>
            </Private>"""

    pedestrian_act = ""
    if cfg.has_pedestrian:
        pedestrian_act = f"""
          <!-- Pedestrian crosses road when ego is close -->
          <Act name="pedestrian_cross">
            <ManeuverGroup maximumExecutionCount="1" name="pedestrian_maneuver">
              <Actors selectTriggeringEntities="false">
                <EntityRef entityRef="pedestrian_0"/>
              </Actors>
              <Maneuver name="cross_road">
                <Event name="start_crossing" priority="overwrite">
                  <Action>
                    <GlobalAction>
                      <EntityAction entityRef="pedestrian_0">
                        <AddEntityAction>
                          <Position>
                            <WorldPosition x="10.0" y="3.0" z="0.0"/>
                          </Position>
                        </AddEntityAction>
                      </EntityAction>
                    </GlobalAction>
                  </Action>
                  <StartTrigger>
                    <ConditionGroup>
                      <Condition name="ego_close" delay="0" conditionEdge="rising">
                        <ByEntityCondition>
                          <TriggeringEntities triggeringEntitiesRule="any">
                            <EntityRef entityRef="ego_vehicle"/>
                          </TriggeringEntities>
                          <EntityCondition>
                            <ReachPositionCondition tolerance="{cfg.trigger_distance}">
                              <Position>
                                <WorldPosition x="10.0" y="0.0" z="0.0"/>
                              </Position>
                            </ReachPositionCondition>
                          </EntityCondition>
                        </ByEntityCondition>
                      </Condition>
                    </ConditionGroup>
                  </StartTrigger>
                </Event>
              </Maneuver>
            </ManeuverGroup>
            <StartTrigger/>
          </Act>"""

    xosc = f"""<?xml version="1.0" encoding="UTF-8"?>
<!--
  OpenSCENARIO scenario generated from nuXmv counterexample
  Property violated : {cfg.property_violated}
  Description       : {cfg.description}
  Generated by      : counterexample_to_scenario.py (CARLA CI/CD Pipeline)
-->
<OpenSCENARIO>

  <FileHeader description="{cfg.description}"
              author="CARLA-CICD-Pipeline"
              revMajor="1" revMinor="0"
              date="2024-01-01T00:00:00"/>

  <ParameterDeclarations>
    <ParameterDeclaration name="EgoVehicle"  parameterType="string"  value="{EGO_BLUEPRINT}"/>
    <ParameterDeclaration name="NpcVehicle"  parameterType="string"  value="{NPC_BLUEPRINT}"/>
    <ParameterDeclaration name="EgoSpeed"    parameterType="double"  value="{cfg.ego_speed}"/>
    <ParameterDeclaration name="NpcSpeed"    parameterType="double"  value="{cfg.npc_speed}"/>
    <ParameterDeclaration name="FollowDist"  parameterType="double"  value="{cfg.following_distance}"/>
  </ParameterDeclarations>

  <RoadNetwork>
    <LogicFile filepath="{cfg.town}"/>
    <SceneGraphFile filepath="{cfg.town}"/>
  </RoadNetwork>

  <Entities>

    <!-- Ego vehicle under test -->
    <ScenarioObject name="ego_vehicle">
      <Vehicle name="{EGO_BLUEPRINT}" vehicleCategory="car">
        <BoundingBox>
          <Center x="1.5" y="0.0" z="0.9"/>
          <Dimensions width="2.1" length="4.5" height="1.8"/>
        </BoundingBox>
        <Performance maxSpeed="69.444" maxAcceleration="200" maxDeceleration="10.0"/>
        <Axles>
          <FrontAxle maxSteering="0.5" wheelDiameter="0.6"
                     trackWidth="1.8" positionX="3.1" positionZ="0.3"/>
          <RearAxle  maxSteering="0.0" wheelDiameter="0.6"
                     trackWidth="1.8" positionX="0.0" positionZ="0.3"/>
        </Axles>
        <Properties>
          <Property name="type" value="ego_vehicle"/>
        </Properties>
      </Vehicle>
    </ScenarioObject>

    <!-- NPC vehicle that triggers the hazardous condition -->
    <ScenarioObject name="npc_vehicle">
      <Vehicle name="{NPC_BLUEPRINT}" vehicleCategory="car">
        <BoundingBox>
          <Center x="1.5" y="0.0" z="0.9"/>
          <Dimensions width="2.0" length="4.2" height="1.6"/>
        </BoundingBox>
        <Performance maxSpeed="69.444" maxAcceleration="200" maxDeceleration="10.0"/>
        <Axles>
          <FrontAxle maxSteering="0.5" wheelDiameter="0.6"
                     trackWidth="1.8" positionX="3.1" positionZ="0.3"/>
          <RearAxle  maxSteering="0.0" wheelDiameter="0.6"
                     trackWidth="1.8" positionX="0.0" positionZ="0.3"/>
        </Axles>
        <Properties>
          <Property name="type" value="simulation"/>
        </Properties>
      </Vehicle>
    </ScenarioObject>
    {pedestrian_block}

  </Entities>

  <Storyboard>

    <!-- Initial positions derived from counterexample state -->
    <Init>
      <Actions>
        <Private entityRef="ego_vehicle">
          <PrivateAction>
            <TeleportAction>
              <Position>
                <WorldPosition x="0.0" y="0.0" z="0.5" h="0"/>
              </Position>
            </TeleportAction>
          </PrivateAction>
          <PrivateAction>
            <LongitudinalAction>
              <SpeedAction>
                <SpeedActionDynamics dynamicsShape="step" value="0" dynamicsDimension="time"/>
                <SpeedActionTarget>
                  <AbsoluteTargetSpeed value="{cfg.ego_speed / 3.6:.3f}"/>
                </SpeedActionTarget>
              </SpeedAction>
            </LongitudinalAction>
          </PrivateAction>
        </Private>

        <!-- NPC starts ahead of ego by following_distance -->
        <Private entityRef="npc_vehicle">
          <PrivateAction>
            <TeleportAction>
              <Position>
                <WorldPosition x="{cfg.following_distance:.1f}" y="0.0" z="0.5" h="0"/>
              </Position>
            </TeleportAction>
          </PrivateAction>
          <PrivateAction>
            <LongitudinalAction>
              <SpeedAction>
                <SpeedActionDynamics dynamicsShape="step" value="0" dynamicsDimension="time"/>
                <SpeedActionTarget>
                  <AbsoluteTargetSpeed value="{cfg.npc_speed / 3.6:.3f}"/>
                </SpeedActionTarget>
              </SpeedAction>
            </LongitudinalAction>
          </PrivateAction>
        </Private>
        {pedestrian_init}
      </Actions>
    </Init>

    <Story name="{cfg.name}_story">
      <Act name="main_act">
        <ManeuverGroup maximumExecutionCount="1" name="npc_maneuver">
          <Actors selectTriggeringEntities="false">
            <EntityRef entityRef="npc_vehicle"/>
          </Actors>
          <Maneuver name="emergency_brake">
            <!-- NPC brakes suddenly when ego is within trigger distance -->
            <Event name="npc_brake_event" priority="overwrite">
              <Action>
                <PrivateAction>
                  <LongitudinalAction>
                    <SpeedAction>
                      <SpeedActionDynamics dynamicsShape="linear"
                                           value="5.0"
                                           dynamicsDimension="rate"/>
                      <SpeedActionTarget>
                        <AbsoluteTargetSpeed value="0"/>
                      </SpeedActionTarget>
                    </SpeedAction>
                  </LongitudinalAction>
                </PrivateAction>
              </Action>
              <StartTrigger>
                <ConditionGroup>
                  <Condition name="ego_close_to_npc" delay="0" conditionEdge="rising">
                    <ByEntityCondition>
                      <TriggeringEntities triggeringEntitiesRule="any">
                        <EntityRef entityRef="ego_vehicle"/>
                      </TriggeringEntities>
                      <EntityCondition>
                        <RelativeDistanceCondition entityRef="npc_vehicle"
                                                   relativeDistanceType="longitudinal"
                                                   value="{cfg.trigger_distance:.1f}"
                                                   freespace="true"
                                                   rule="lessThan"/>
                      </EntityCondition>
                    </ByEntityCondition>
                  </Condition>
                </ConditionGroup>
              </StartTrigger>
            </Event>
          </Maneuver>
        </ManeuverGroup>

        <StartTrigger>
          <ConditionGroup>
            <Condition name="start_immediately" delay="0" conditionEdge="none">
              <ByValueCondition>
                <SimulationTimeCondition value="0" rule="greaterThan"/>
              </ByValueCondition>
            </Condition>
          </ConditionGroup>
        </StartTrigger>
      </Act>
      {pedestrian_act}
    </Story>

    <!-- End scenario after 30 seconds or on collision -->
    <StopTrigger>
      <ConditionGroup>
        <Condition name="timeout" delay="0" conditionEdge="rising">
          <ByValueCondition>
            <SimulationTimeCondition value="30" rule="greaterThan"/>
          </ByValueCondition>
        </Condition>
      </ConditionGroup>
    </StopTrigger>

  </Storyboard>

</OpenSCENARIO>
"""
    return xosc


def main():
    parser = argparse.ArgumentParser(
        description="Convert nuXmv counterexamples to OpenSCENARIO files"
    )
    parser.add_argument('--counterexamples', required=True,
                        help='Input JSON from run_model_checker.py')
    parser.add_argument('--output-dir', required=True,
                        help='Directory to write .xosc scenario files')
    args = parser.parse_args()

    with open(args.counterexamples) as f:
        data = json.load(f)

    os.makedirs(args.output_dir, exist_ok=True)

    raw_results = data.get('raw_results', [])
    violated    = [r for r in raw_results if r.get('result') == 'violated']

    print(f"Converting {len(violated)} counterexamples to OpenSCENARIO...")

    generated = []
    for i, result in enumerate(violated):
        ce  = result.get('counterexample', {})
        cfg = extract_scenario_config(ce, i + 1)

        xosc_content = generate_xosc(cfg)
        filename     = f"{cfg.name}.xosc"
        filepath     = os.path.join(args.output_dir, filename)

        with open(filepath, 'w') as f:
            f.write(xosc_content)

        generated.append({
            "file": filepath,
            "property_violated": cfg.property_violated,
            "description": cfg.description,
            "ego_speed": cfg.ego_speed,
            "following_distance": cfg.following_distance,
            "has_pedestrian": cfg.has_pedestrian,
        })

        print(f"  ✅ {filename} — {cfg.description}")

    # Save manifest
    manifest_path = os.path.join(args.output_dir, 'scenarios_manifest.json')
    with open(manifest_path, 'w') as f:
        json.dump({
            "total_scenarios": len(generated),
            "scenarios": generated
        }, f, indent=2)

    print(f"\n{len(generated)} OpenSCENARIO files written to: {args.output_dir}")
    print(f"Manifest saved to: {manifest_path}")


if __name__ == '__main__':
    main()

