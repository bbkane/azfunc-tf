Generally to iterate, copy the function directory and rename it. For the README, I've renamed it to "function_app_name" here (which happens to be an illegal URL, but whatever).


```
function_app_hostname = "function_app_name.azurewebsites.net"
function_app_name = "function_app_name"
```

```
func azure functionapp publish function_app_name
```

```
Invoke url: https://function_app_name.azurewebsites.net/api/my_function
```

Can also start from scratch with something like:

```
# from ./06_more_auth_from_tf
func init fnhw06-01-fa-dev-bbk --python
cd fnhw06-01-fa-dev-bbk
python3 -m venv venv
source ./venv/bin/activate
pip install -r requirements.txt
pip freeze --requirement requirements.txt | sponge requirements.txt
func new --name fnhw06-01-fn-dev-bbk --template "HTTP trigger" --authlevel "anonymous"
func start  # test locally
curl http://localhost:7071/api/fnhw06-01-fn-dev-bbk
```

```
func azure functionapp publish fnhw06-01-fa-dev-bbk
```

NOTE that the newlines will be `\r\n` instead of `\n` - see https://github.com/Azure/azure-functions-core-tools/issues/2673

That's probably enough for now...
