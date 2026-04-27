"""
Create sample MuleSoft repositories with intentional bugs for demo
Uses GitHub API to create repos directly in your organization
"""
import requests
import base64
import json
import time
from typing import Dict, List
from pathlib import Path
import sys
import os

# Add parent directory to path to import config
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import get_settings


class SampleRepoCreator:
    """Creates sample MuleSoft repositories with intentional bugs"""
    
    def __init__(self):
        self.settings = get_settings()
        self.org = self.settings.github_org
        self.token = self.settings.github_token
        self.base_url = "https://api.github.com"
        self.headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json"
        }
        
    def create_all_repos(self):
        """Create all 3 sample MuleSoft repositories"""
        repos = [
            {
                "name": "mulesoft-payment-service",
                "type": "payment"
            },
            {
                "name": "mulesoft-order-service",
                "type": "order"
            },
            {
                "name": "mulesoft-inventory-service",
                "type": "inventory"
            }
        ]
        
        print("=" * 70)
        print("MULESOFT SAMPLE REPOSITORY CREATOR")
        print("=" * 70)
        print(f"\nOrganization: {self.org}")
        print(f"Creating {len(repos)} repositories with intentional bugs\n")
        
        created_repos = []
        
        for repo_info in repos:
            repo_name = repo_info["name"]
            repo_type = repo_info["type"]
            
            print(f"\n{'='*70}")
            print(f"Creating repository: {repo_name}")
            print(f"{'='*70}")
            
            try:
                # Create repo
                if self.create_repo(repo_name):
                    print(f"[OK] Repository created")
                    
                    # Wait a moment for GitHub to initialize
                    time.sleep(2)
                    
                    # Add all files
                    self.populate_repo(repo_name, repo_type)
                    
                    print(f"\n[OK] Repository {repo_name} populated successfully!")
                    created_repos.append(repo_name)
                else:
                    print(f"[FAIL] Failed to create repository {repo_name}")
                    
            except Exception as e:
                print(f"[ERROR] Error creating {repo_name}: {str(e)}")
        
        print("\n" + "=" * 70)
        print(f"SUMMARY: {len(created_repos)}/{len(repos)} repositories created")
        print("=" * 70)
        
        if created_repos:
            print("\nCreated repositories:")
            for repo in created_repos:
                print(f"  • https://github.com/{self.org}/{repo}")
            
            # Generate mapping config
            self.generate_mapping_config(created_repos)
        
        print("\nNext steps:")
        print("  1. Review created repositories on GitHub")
        print("  2. Update config/app_repo_mapping.yaml (auto-generated)")
        print("  3. Run error simulator to test integration")
    
    def create_repo(self, repo_name: str) -> bool:
        """Create a new GitHub repository"""
        url = f"{self.base_url}/orgs/{self.org}/repos"
        
        data = {
            "name": repo_name,
            "description": f"Sample MuleSoft app with intentional bugs for AI fix demo",
            "private": False,
            "auto_init": False,  # We'll add files manually
            "has_issues": True,
            "has_projects": False,
            "has_wiki": False
        }
        
        response = requests.post(url, json=data, headers=self.headers)
        
        if response.status_code == 201:
            return True
        elif response.status_code == 422:
            # Repo might already exist
            print(f"  Repository might already exist, continuing...")
            return True
        else:
            print(f"  Error: {response.status_code} - {response.text}")
            return False
    
    def populate_repo(self, repo_name: str, repo_type: str):
        """Add all files to the repository"""
        files = self.get_repo_files(repo_type)
        
        for file_path, file_content in files.items():
            print(f"  Creating: {file_path}")
            if not self.create_file(repo_name, file_path, file_content):
                print(f"    [WARN] Failed to create {file_path}")
            time.sleep(0.5)  # Rate limiting
    
    def create_file(self, repo_name: str, file_path: str, content: str) -> bool:
        """Create a single file in the repository"""
        url = f"{self.base_url}/repos/{self.org}/{repo_name}/contents/{file_path}"
        
        # Encode content to base64
        content_bytes = content.encode('utf-8')
        content_base64 = base64.b64encode(content_bytes).decode('utf-8')
        
        data = {
            "message": f"Add {file_path}",
            "content": content_base64,
            "branch": "main"
        }
        
        response = requests.put(url, json=data, headers=self.headers)
        
        if response.status_code in [201, 200]:
            return True
        else:
            # Try to get the file first (might exist)
            get_response = requests.get(url, headers=self.headers)
            if get_response.status_code == 200:
                # File exists, update it
                sha = get_response.json()['sha']
                data['sha'] = sha
                data['message'] = f"Update {file_path}"
                update_response = requests.put(url, json=data, headers=self.headers)
                return update_response.status_code == 200
            return False
    
    def get_repo_files(self, repo_type: str) -> Dict[str, str]:
        """Get all files for specific repository type"""
        
        # Common files for all repos
        common_files = {
            "README.md": self.get_readme_content(repo_type),
            "pom.xml": self.get_pom_content(repo_type),
            ".gitignore": self.get_gitignore_content()
        }
        
        # Type-specific files
        if repo_type == "payment":
            specific_files = self.get_payment_service_files()
        elif repo_type == "order":
            specific_files = self.get_order_service_files()
        elif repo_type == "inventory":
            specific_files = self.get_inventory_service_files()
        else:
            specific_files = {}
        
        return {**common_files, **specific_files}
    
    def get_payment_service_files(self) -> Dict[str, str]:
        """Files for payment service with intentional bugs"""
        return {
            "src/main/mule/payment-processing.xml": '''<?xml version="1.0" encoding="UTF-8"?>
<mule xmlns="http://www.mulesoft.org/schema/mule/core"
      xmlns:http="http://www.mulesoft.org/schema/mule/http"
      xmlns:db="http://www.mulesoft.org/schema/mule/db"
      xmlns:ee="http://www.mulesoft.org/schema/mule/ee/core">

    <!-- ❌ BUG: NullPointerException - customer object can be null -->
    <flow name="processPaymentFlow">
        <http:listener path="/api/payments" method="POST"/>
        
        <db:select config-ref="Database_Config">
            <db:sql>SELECT * FROM customers WHERE id = :customerId</db:sql>
            <db:input-parameters>
                <![CDATA[#[{customerId: payload.customerId}]]]>
            </db:input-parameters>
        </db:select>
        
        <!-- BUG: Accessing customer.name without null check -->
        <logger level="INFO" message="Processing payment for customer: #[payload[0].name]"/>
        
        <ee:transform>
            <ee:message>
                <ee:set-payload resource="dataweave/transform-payment-request.dwl"/>
            </ee:message>
        </ee:transform>
    </flow>
</mule>''',
            
            "src/main/mule/card-validation.xml": '''<?xml version="1.0" encoding="UTF-8"?>
<mule xmlns="http://www.mulesoft.org/schema/mule/core"
      xmlns:ee="http://www.mulesoft.org/schema/mule/ee/core">

    <!-- ❌ BUG: ArrayIndexOutOfBoundsException in card validation -->
    <flow name="validateCardFlow">
        <set-variable variableName="cardParts" value="#[payload.cardNumber splitBy('-')]"/>
        
        <!-- BUG: Assumes card has 4 parts, doesn't validate array length -->
        <logger level="INFO" message="Card part 1: #[vars.cardParts[0]]"/>
        <logger level="INFO" message="Card part 2: #[vars.cardParts[1]]"/>
        <logger level="INFO" message="Card part 3: #[vars.cardParts[2]]"/>
        <logger level="INFO" message="Card part 4: #[vars.cardParts[3]]"/>
    </flow>
</mule>''',
            
            "src/main/resources/dataweave/transform-payment-request.dwl": '''%dw 2.0
output application/json
---
{
  transactionId: payload.transaction.id,
  customerId: payload.customer.id,
  amount: payload.payment.amount,
  currency: payload.payment.currency,
  // ❌ BUG: payload.customer.address can be null
  billingAddress: {
    street: payload.customer.address.street,
    city: payload.customer.address.city,
    zipCode: payload.customer.address.zipCode
  },
  timestamp: now()
}''',
            
            "src/main/resources/dataweave/map-customer-data.dwl": '''%dw 2.0
output application/json
---
{
  customers: payload.customers map (customer, index) -> {
    id: customer.id,
    name: customer.firstName ++ " " ++ customer.lastName,
    // ❌ BUG: customer.accountBalance might be null or string
    balance: customer.accountBalance * 1.15,
    status: upper(customer.status)
  }
}''',
            
            "src/main/resources/dataweave/aggregate-transactions.dwl": '''%dw 2.0
output application/json
---
{
  summary: {
    totalTransactions: sizeOf(payload.transactions),
    // ❌ BUG: Assumes transactions array always has items
    firstTransaction: payload.transactions[0].id,
    lastTransaction: payload.transactions[-1].id,
    totalAmount: sum(payload.transactions map $.amount)
  }
}''',
            
            "src/main/resources/properties/config-dev.yaml": '''# Database Configuration
database:
  host: localhost
  port: 3306
  name: payment_db
  username: dev_user
  # ❌ BUG: Incorrect indentation
    password: dev_pass123
  connection-pool:
    max-size: 10
    min-size: 2

# API Configuration
api:
  base-url: http://localhost:8081
  timeout: 30000
  # ❌ BUG: Missing quotes for string with special chars
  api-key: test-key-@#$%''',
            
            "src/main/resources/properties/config-prod.yaml": '''# Production Configuration
database:
  host: prod-db-server.company.com
  port: 3306
  name: payment_prod_db
  username: ${DB_USERNAME}
  # ❌ BUG: Password property missing

# API Configuration
api:
  base-url: https://api.company.com
  timeout: 60000
  # ❌ BUG: Missing api-key property''',
        }
    
    def get_order_service_files(self) -> Dict[str, str]:
        """Files for order service"""
        return {
            "src/main/mule/order-flows.xml": '''<?xml version="1.0" encoding="UTF-8"?>
<mule xmlns="http://www.mulesoft.org/schema/mule/core"
      xmlns:db="http://www.mulesoft.org/schema/mule/db">

    <!-- ❌ BUG: OutOfMemoryError - loading too many records -->
    <flow name="getTransactionHistoryFlow">
        <db:select config-ref="Database_Config">
            <!-- BUG: No pagination, loads all records -->
            <db:sql>SELECT * FROM transactions</db:sql>
        </db:select>
        
        <logger level="INFO" message="Loaded #[sizeOf(payload)] transactions"/>
    </flow>
</mule>''',
            
            "src/main/resources/dataweave/transform-order.dwl": '''%dw 2.0
output application/json
---
{
  orderId: payload.id,
  // ❌ BUG: Missing default value
  customerName: payload.customer.name,
  items: payload.items map {
    productId: $.productId,
    quantity: $.quantity
  }
}''',
            
            "src/main/resources/properties/config.yaml": '''# Order Service Configuration
database:
  host: localhost
  port: 3306
# ❌ BUG: Missing required 'name' field

api:
  timeout: "30000"  # ❌ BUG: Should be integer, not string''',
        }
    
    def get_inventory_service_files(self) -> Dict[str, str]:
        """Files for inventory service"""
        return {
            "src/main/mule/inventory.xml": '''<?xml version="1.0" encoding="UTF-8"?>
<mule xmlns="http://www.mulesoft.org/schema/mule/core"
      xmlns:db="http://www.mulesoft.org/schema/mule/db">

    <!-- ❌ BUG: ConcurrentModificationException -->
    <flow name="updateInventoryFlow">
        <db:update config-ref="Database_Config">
            <!-- BUG: No optimistic locking -->
            <db:sql>UPDATE inventory SET quantity = :qty WHERE product_id = :id</db:sql>
        </db:update>
    </flow>
</mule>''',
            
            "src/main/resources/dataweave/transform-inventory.dwl": '''%dw 2.0
output application/json
---
{
  items: payload.items map {
    productId: $.id,
    // ❌ BUG: Array access without bounds check
    stock: $.stockLevels[0].quantity
  }
}''',
            
            "src/main/resources/properties/config.yaml": '''# Inventory Service Configuration
database:
  host: localhost
  port: 3306
  name: inventory_db
  # ❌ BUG: Missing username and password

api:
  enabled: "yes"  # ❌ BUG: Should be boolean, not string''',
        }
    
    def get_readme_content(self, repo_type: str) -> str:
        """Generate README content"""
        return f'''# MuleSoft {repo_type.title()} Service

Sample MuleSoft application with **intentional bugs** for AI-powered fix demo.

## ⚠️ Intentional Bugs

This repository contains intentional bugs for demonstration purposes:

### Java/Mule Errors
- NullPointerException handling
- Array index out of bounds
- Database connection issues
- Concurrent modification

### DataWeave Errors
- Null pointer in transformations
- Type mismatches
- Array access without bounds checking
- Missing default values

### YAML Configuration Errors
- Invalid indentation
- Missing required properties
- Type mismatches (string vs int)
- Missing environment variables

## Purpose

This repository is used by an AI-powered monitoring system that:
1. Detects errors from production logs
2. Fetches the relevant source code
3. Generates fixes automatically
4. Creates pull requests with inline comments

## Structure

```
src/
├── main/
│   ├── mule/              # MuleSoft flow definitions
│   └── resources/
│       ├── dataweave/     # DataWeave transformations
│       └── properties/    # YAML configurations
└── test/
    └── java/              # Unit tests
```

## Note

**Do not use this code in production!** It contains intentional bugs for demo purposes.
'''
    
    def get_pom_content(self, repo_type: str) -> str:
        """Generate pom.xml content"""
        artifact_id = f"mulesoft-{repo_type}-service"
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 
         http://maven.apache.org/xsd/maven-4.0.0.xsd">
    
    <modelVersion>4.0.0</modelVersion>
    <groupId>com.company.mulesoft</groupId>
    <artifactId>{artifact_id}</artifactId>
    <version>1.0.0-SNAPSHOT</version>
    <packaging>mule-application</packaging>
    <name>{artifact_id}</name>
    
    <properties>
        <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
        <mule.version>4.4.0</mule.version>
        <mule.maven.plugin.version>3.8.0</mule.maven.plugin.version>
    </properties>
    
    <build>
        <plugins>
            <plugin>
                <groupId>org.mule.tools.maven</groupId>
                <artifactId>mule-maven-plugin</artifactId>
                <version>${{mule.maven.plugin.version}}</version>
                <extensions>true</extensions>
            </plugin>
        </plugins>
    </build>
    
    <dependencies>
        <dependency>
            <groupId>org.mule.connectors</groupId>
            <artifactId>mule-http-connector</artifactId>
            <version>1.7.1</version>
            <classifier>mule-plugin</classifier>
        </dependency>
        <dependency>
            <groupId>org.mule.connectors</groupId>
            <artifactId>mule-db-connector</artifactId>
            <version>1.13.4</version>
            <classifier>mule-plugin</classifier>
        </dependency>
    </dependencies>
    
    <repositories>
        <repository>
            <id>mulesoft-releases</id>
            <name>MuleSoft Releases Repository</name>
            <url>https://repository.mulesoft.org/releases/</url>
        </repository>
    </repositories>
</project>
'''
    
    def get_gitignore_content(self) -> str:
        """Generate .gitignore content"""
        return '''# MuleSoft
target/
.mule/
.studio/
*.zip

# IDE
.idea/
.vscode/
*.iml
.DS_Store

# Build
*.class
*.jar
*.war

# Logs
logs/
*.log
'''
    
    def generate_mapping_config(self, created_repos: List[str]):
        """Generate app_repo_mapping.yaml configuration"""
        
        mapping = {
            "app_mappings": {}
        }
        
        for repo_name in created_repos:
            if "payment" in repo_name:
                app_name = "payment-processing-app"
                code_paths = [
                    "src/main/mule/payment-processing.xml",
                    "src/main/mule/card-validation.xml",
                    "src/main/resources/dataweave/transform-payment-request.dwl",
                    "src/main/resources/dataweave/map-customer-data.dwl",
                    "src/main/resources/dataweave/aggregate-transactions.dwl",
                    "src/main/resources/properties/config-dev.yaml",
                    "src/main/resources/properties/config-prod.yaml"
                ]
            elif "order" in repo_name:
                app_name = "order-management-app"
                code_paths = [
                    "src/main/mule/order-flows.xml",
                    "src/main/resources/dataweave/transform-order.dwl",
                    "src/main/resources/properties/config.yaml"
                ]
            elif "inventory" in repo_name:
                app_name = "inventory-app"
                code_paths = [
                    "src/main/mule/inventory.xml",
                    "src/main/resources/dataweave/transform-inventory.dwl",
                    "src/main/resources/properties/config.yaml"
                ]
            else:
                continue
            
            mapping["app_mappings"][app_name] = {
                "repo": f"{self.org}/{repo_name}",
                "code_paths": code_paths,
                "primary_language": "java",
                "mule_version": "4.4.0"
            }
        
        # Write to config file
        import yaml
        config_path = Path(__file__).parent.parent / "config" / "app_repo_mapping.yaml"
        with open(config_path, 'w') as f:
            yaml.dump(mapping, f, default_flow_style=False, sort_keys=False)
        
        print(f"\n[OK] Generated config/app_repo_mapping.yaml")


def main():
    """Main entry point"""
    try:
        creator = SampleRepoCreator()
        creator.create_all_repos()
    except Exception as e:
        print(f"\n[ERROR] Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    exit(main())
