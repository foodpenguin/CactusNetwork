// SPDX-License-Identifier: MIT
pragma solidity ^0.8.25;

import "forge-std/Script.sol";
import "forge-std/console.sol";
import "../src/IntentVault.sol";
import "../src/DarkPoolOTC.sol";
import "../src/SettlementRouter.sol";
import "../src/ProtocolTreasury.sol";

contract DeployCactusNetwork is Script {
    function run() external {
        uint256 deployerPrivateKey = vm.envUint("DEPLOYMENT_PRIVATE_KEY");
        address wethAddress = vm.envOr("WETH_ADDRESS", address(0)); 
        address uniswapRouterAddress = vm.envOr("UNISWAP_ROUTER_ADDRESS", address(0));
        address keeperAddress = vm.envOr("KEEPER_ADDRESS", address(0));
        
        require(wethAddress != address(0), "Please set WETH_ADDRESS in .env");
        require(uniswapRouterAddress != address(0), "Please set UNISWAP_ROUTER_ADDRESS in .env");

        vm.startBroadcast(deployerPrivateKey);

        console.log("Deploying IntentVault...");
        IntentVault vault = new IntentVault(wethAddress);
        console.log("IntentVault deployed at:", address(vault));

        console.log("Deploying DarkPoolOTC...");
        DarkPoolOTC otc = new DarkPoolOTC();
        console.log("DarkPoolOTC deployed at:", address(otc));

        console.log("Deploying SettlementRouter...");
        SettlementRouter router = new SettlementRouter(address(vault), address(otc), uniswapRouterAddress);
        console.log("SettlementRouter deployed at:", address(router));

        console.log("Deploying ProtocolTreasury...");
        ProtocolTreasury treasury = new ProtocolTreasury(address(router));
        console.log("ProtocolTreasury deployed at:", address(treasury));

        console.log("Setting up contract permissions...");
        vault.setSettlementRouter(address(router));
        otc.setSettlementRouter(address(router));
        router.setTreasury(address(treasury));
        
        if (keeperAddress != address(0)) {
            treasury.addKeeper(keeperAddress);
        }

        vm.stopBroadcast();

        console.log("=== Deployment Complete ===");
        console.log("Please update your frontend .env with the following addresses:");
        console.log("VAULT_ADDRESS=", address(vault));
        console.log("OTC_ADDRESS=", address(otc));
        console.log("ROUTER_ADDRESS=", address(router));
        console.log("TREASURY_ADDRESS=", address(treasury));
    }
}
