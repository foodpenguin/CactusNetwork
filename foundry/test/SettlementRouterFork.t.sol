// SPDX-License-Identifier: MIT
pragma solidity ^0.8.25;

import "forge-std/Test.sol";
import "forge-std/console.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "../src/IntentVault.sol";
import "../src/DarkPoolOTC.sol";
import "../src/SettlementRouter.sol";
import "../src/ProtocolTreasury.sol";

// Minimal Uniswap V3 SwapRouter02 interface
interface ISwapRouter {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24 fee;
        address recipient;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }
    
    function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut);
}

contract SettlementRouterForkTest is Test {
    IntentVault public vault;
    DarkPoolOTC public otc;
    SettlementRouter public router;
    ProtocolTreasury public treasury;

    // Ethereum Mainnet addresses
    address constant MAINNET_WETH = 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2;
    address constant MAINNET_USDC = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;
    address constant UNISWAP_ROUTER = 0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45;

    uint256 internal userAPrivateKey = 0xA11CE;
    address public userA = vm.addr(userAPrivateKey);
    address public keeper = address(0x123);

    bytes32 constant DOMAIN_TYPEHASH = keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)");
    bytes32 DOMAIN_SEPARATOR;

    function setUp() public {
        // Fork Ethereum mainnet for real Uniswap liquidity
        vm.createSelectFork("https://eth.drpc.org");

        vault = new IntentVault(MAINNET_WETH);
        otc = new DarkPoolOTC();
        router = new SettlementRouter(address(vault), address(otc), UNISWAP_ROUTER);
        treasury = new ProtocolTreasury(address(router));

        vault.setSettlementRouter(address(router));
        otc.setSettlementRouter(address(router));
        router.setTreasury(address(treasury));
        treasury.addKeeper(keeper);

        DOMAIN_SEPARATOR = keccak256(
            abi.encode(
                DOMAIN_TYPEHASH,
                keccak256(bytes("SettlementRouter")),
                keccak256(bytes("1")),
                block.chainid,
                address(router)
            )
        );

        deal(MAINNET_WETH, userA, 1 ether);
    }

    function _signIntent(UserIntent memory intent, uint256 privateKey) internal view returns (bytes memory) {
        bytes32 intentHash = router.hashIntent(intent);
        bytes32 digest = keccak256(abi.encodePacked("\x19\x01", DOMAIN_SEPARATOR, intentHash));
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(privateKey, digest);
        return abi.encodePacked(r, s, v);
    }

    function test_ActionType0_DEX_Fork() public {
        uint256 executeAmount = 0.01 ether;

        // Deposit 0.01 WETH into vault
        vm.startPrank(userA);
        IERC20(MAINNET_WETH).approve(address(vault), executeAmount);
        vault.deposit(MAINNET_WETH, executeAmount);
        vm.stopPrank();

        assertEq(vault.balances(userA, MAINNET_WETH), executeAmount);

        // Intent: swap 0.01 WETH for >= 10 USDC (6 decimals)
        UserIntent memory intentA = UserIntent({
            user: userA,
            tokenIn: MAINNET_WETH,
            tokenOut: MAINNET_USDC,
            amountIn: executeAmount,
            minAmountOut: 10 * 10**6, 
            deadline: block.timestamp + 1 hours,
            salt: bytes32(uint256(999)),
            allowPartialFill: true
        });
        bytes memory sigA = _signIntent(intentA, userAPrivateKey);

        // Build Uniswap V3 exactInputSingle calldata
        bytes memory dexCalldata = abi.encodeWithSignature(
            "exactInputSingle((address,address,uint24,address,uint256,uint256,uint160))",
            ISwapRouter.ExactInputSingleParams({
                tokenIn: MAINNET_WETH,
                tokenOut: MAINNET_USDC,
                fee: 500,        // 0.05% pool (deepest WETH/USDC)
                recipient: userA, // Router checks user's balance delta
                amountIn: executeAmount,
                amountOutMinimum: 0,
                sqrtPriceLimitX96: 0
            })
        );

        bytes memory executionData = abi.encode(dexCalldata);

        // Execute via keeper (actionType = 0)
        uint256 userUsdcBalanceBefore = IERC20(MAINNET_USDC).balanceOf(userA);
        
        vm.startPrank(keeper);
        treasury.forwardExecuteIntent(intentA, sigA, 0, executionData, executeAmount);
        vm.stopPrank();

        uint256 receivedUsdc = IERC20(MAINNET_USDC).balanceOf(userA) - userUsdcBalanceBefore;
        
        console.log("Swapped 0.01 WETH -> USDC on Mainnet fork");
        console.log("USDC Received:", receivedUsdc);

        assertGt(receivedUsdc, intentA.minAmountOut, "Should receive more than minAmountOut");
        assertEq(vault.balances(userA, MAINNET_WETH), 0, "Vault WETH should be deducted");
    }
}
