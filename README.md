This is a Python script that monitors Ethereum mainnet for newly deployed contracts
and prints qualifying deployments in the terminal. 


#FEATURES
-Detects both deployments from various factories and direct deployments
-Uses default minimum balance of .5E (from the funder)
-displays contract name and supply 
-shows sender and creator addresses
-looks up observed ETH funding sources through Etherscan
-also notifies whether contract is verified or not

#reqs
-will need Etherscan API KEY
-quicknode APIURL

