"""Debug the workflow graph structure."""
from agents.workflow import create_agent_workflow

# Create workflow
workflow = create_agent_workflow()

print("Workflow Graph Structure:")
print("=" * 60)

# Check if workflow has the nodes
if hasattr(workflow, 'nodes'):
    print(f"\nNodes: {list(workflow.nodes.keys())}")

# Check edges
if hasattr(workflow, 'graph'):
    print(f"\nGraph object: {workflow.graph}")
    if hasattr(workflow.graph, 'edges'):
        print(f"Edges: {workflow.graph.edges}")

# Try to get the workflow structure
print(f"\nWorkflow type: {type(workflow)}")
print(f"Workflow dir: {[x for x in dir(workflow) if not x.startswith('_')]}")

# Check the actual graph
try:
    graph_dict = workflow.get_graph().to_json()
    print(f"\nGraph structure type: {type(graph_dict)}")
    print(f"Graph keys: {graph_dict.keys() if hasattr(graph_dict, 'keys') else 'N/A'}")
    
    # Print nodes
    nodes = graph_dict.get('nodes', [])
    print(f"\nNodes ({len(nodes)} total):")
    if isinstance(nodes, list):
        for node in nodes:
            print(f"  - {node.get('id', node)}")
    else:
        for node_id in nodes.keys():
            print(f"  - {node_id}")
    
    # Print edges
    edges = graph_dict.get('edges', [])
    print(f"\nEdges ({len(edges)} total):")
    for edge in edges:
        source = edge.get('source')
        target = edge.get('target')
        print(f"  {source} -> {target}")
except Exception as e:
    print(f"\nError getting graph structure: {e}")
    import traceback
    traceback.print_exc()
