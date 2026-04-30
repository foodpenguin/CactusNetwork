// SPDX-License-Identifier: MIT
pragma solidity ^0.8.25;

import "forge-std/Test.sol";
import "forge-std/console.sol";
import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "../src/IntentVault.sol";
import "../src/DarkPoolOTC.sol";
import "../src/SettlementRouter.sol";
import "../src/ProtocolTreasury.sol";

contract MockToken is ERC20 {
    constructor(string memory name, string memory symbol) ERC20(name, symbol) {}
    function mint(address to, uint256 amount) external { _mint(to, amount); }
}

contract MockWETH is ERC20 {
    constructor() ERC20("Wrapped ETH", "WETH") {}
    function deposit() external payable { _mint(msg.sender, msg.value); }
    function withdraw(uint256 amount) external {
        _burn(msg.sender, amount);
        (bool success, ) = msg.sender.call{value: amount}("");
        require(success, "ETH transfer failed");
    }
}

contract SettlementRouterTest is Test {
    IntentVault public vault;
    DarkPoolOTC public otc;
    SettlementRouter public router;
    ProtocolTreasury public treasury;

    MockWETH public weth;
    MockToken public usdc;

    uint256 internal userAPrivateKey = 0xA11CE;
    address public userA = vm.addr(userAPrivateKey);
    uint256 internal userBPrivateKey = 0xB0B;
    address public userB = vm.addr(userBPrivateKey);
    address public keeper = address(0x123);
    address public uniswapRouterMock = address(0x456);

    bytes32 constant DOMAIN_TYPEHASH = keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)");
    bytes32 DOMAIN_SEPARATOR;

    function setUp() public {
        weth = new MockWETH();
        usdc = new MockToken("USD Coin", "USDC");

        vault = new IntentVault(address(weth));
        otc = new DarkPoolOTC();
        router = new SettlementRouter(address(vault), address(otc), uniswapRouterMock);
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

        vm.deal(userA, 10 ether);
        vm.deal(userB, 10 ether);
        usdc.mint(userB, 1000000 * 10**18);
        usdc.mint(address(treasury), 1000000 * 10**18);
    }

    function _signIntent(UserIntent memory intent, uint256 privateKey) internal view returns (bytes memory) {
        bytes32 intentHash = router.hashIntent(intent);
        bytes32 digest = keccak256(abi.encodePacked("\x19\x01", DOMAIN_SEPARATOR, intentHash));
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(privateKey, digest);
        return abi.encodePacked(r, s, v);
    }

    // ── Unit Tests ──

    function test_VaultDepositETH() public {
        vm.startPrank(userA);
        vault.depositETH{value: 1 ether}();
        vm.stopPrank();

        assertEq(weth.balanceOf(address(vault)), 1 ether);
        assertEq(vault.balances(userA, address(weth)), 1 ether);
    }

    function test_IntentHashing() public {
        UserIntent memory intent = UserIntent({
            user: userA,
            tokenIn: address(weth),
            tokenOut: address(usdc),
            amountIn: 1 ether,
            minAmountOut: 3000 * 10**18,
            deadline: block.timestamp + 1 hours,
            salt: bytes32(uint256(1)),
            allowPartialFill: true
        });

        bytes32 hash1 = router.hashIntent(intent);
        intent.salt = bytes32(uint256(2));
        bytes32 hash2 = router.hashIntent(intent);
        assertTrue(hash1 != hash2, "Hashes should differ with different salt");
    }

    // ── Workflow Tests ──

    function test_ActionType1_OTC() public {
        // Deposit: A deposits 1 WETH, B deposits 3100 USDC
        vm.startPrank(userA);
        vault.depositETH{value: 1 ether}();
        vm.stopPrank();

        vm.startPrank(userB);
        usdc.approve(address(vault), 3100 * 10**18);
        vault.deposit(address(usdc), 3100 * 10**18);
        vm.stopPrank();

        // Intent A: sell 1 WETH, want >= 3000 USDC
        UserIntent memory intentA = UserIntent({
            user: userA,
            tokenIn: address(weth),
            tokenOut: address(usdc),
            amountIn: 1 ether,
            minAmountOut: 3000 * 10**18,
            deadline: block.timestamp + 1 hours,
            salt: bytes32(uint256(1)),
            allowPartialFill: true
        });
        bytes memory sigA = _signIntent(intentA, userAPrivateKey);

        // Intent B: sell 3100 USDC, want >= 1 WETH
        UserIntent memory intentB = UserIntent({
            user: userB,
            tokenIn: address(usdc),
            tokenOut: address(weth),
            amountIn: 3100 * 10**18,
            minAmountOut: 1 ether,
            deadline: block.timestamp + 1 hours,
            salt: bytes32(uint256(2)),
            allowPartialFill: true
        });
        bytes memory sigB = _signIntent(intentB, userBPrivateKey);

        // Keeper executes OTC match (actionType = 1)
        bytes memory executionData = abi.encode(intentB, sigB, 3100 * 10**18);

        vm.startPrank(keeper);
        treasury.forwardExecuteIntent(intentA, sigA, 1, executionData, 1 ether);
        vm.stopPrank();

        assertEq(weth.balanceOf(userB), 1 ether, "UserB should receive WETH");
        assertEq(usdc.balanceOf(userA), 3100 * 10**18, "UserA should receive USDC");
        assertEq(vault.balances(userA, address(weth)), 0, "Vault A WETH should be 0");
        assertEq(vault.balances(userB, address(usdc)), 0, "Vault B USDC should be 0");
    }

    function test_ActionType2_Treasury() public {
        vm.startPrank(userA);
        vault.depositETH{value: 1 ether}();
        vm.stopPrank();

        // Intent A: sell 1 WETH, want >= 3000 USDC
        UserIntent memory intentA = UserIntent({
            user: userA,
            tokenIn: address(weth),
            tokenOut: address(usdc),
            amountIn: 1 ether,
            minAmountOut: 3000 * 10**18,
            deadline: block.timestamp + 1 hours,
            salt: bytes32(uint256(1)),
            allowPartialFill: true
        });
        bytes memory sigA = _signIntent(intentA, userAPrivateKey);

        // AI decides: give 3050 USDC from treasury (actionType = 2)
        bytes memory executionData = abi.encode(3050 * 10**18);

        vm.startPrank(keeper);
        treasury.forwardExecuteIntent(intentA, sigA, 2, executionData, 1 ether);
        vm.stopPrank();

        assertEq(usdc.balanceOf(userA), 3050 * 10**18, "UserA should receive USDC from Treasury");
        assertEq(weth.balanceOf(address(treasury)), 1 ether, "Treasury should receive WETH from UserA");
    }

    function test_PartialFill_Success() public {
        vm.startPrank(userA);
        vault.depositETH{value: 1 ether}();
        vm.stopPrank();

        // Intent: 1 WETH -> >= 3000 USDC, partial fill allowed
        UserIntent memory intentA = UserIntent({
            user: userA,
            tokenIn: address(weth),
            tokenOut: address(usdc),
            amountIn: 1 ether,
            minAmountOut: 3000 * 10**18,
            deadline: block.timestamp + 1 hours,
            salt: bytes32(uint256(1)),
            allowPartialFill: true
        });
        bytes memory sigA = _signIntent(intentA, userAPrivateKey);

        // First fill: 0.5 WETH -> 1500 USDC
        vm.startPrank(keeper);
        treasury.forwardExecuteIntent(intentA, sigA, 2, abi.encode(1500 * 10**18), 0.5 ether);
        vm.stopPrank();

        bytes32 intentHash = router.hashIntent(intentA);
        assertEq(router.filledAmountIn(intentHash), 0.5 ether, "Should be half filled");
        assertEq(usdc.balanceOf(userA), 1500 * 10**18, "Should receive half USDC");

        // Second fill: remaining 0.5 WETH -> 1500 USDC
        vm.startPrank(keeper);
        treasury.forwardExecuteIntent(intentA, sigA, 2, abi.encode(1500 * 10**18), 0.5 ether);
        vm.stopPrank();

        assertEq(router.filledAmountIn(intentHash), 1 ether, "Should be fully filled");
        assertEq(usdc.balanceOf(userA), 3000 * 10**18, "Should receive full USDC");
    }

    function test_PartialFill_Revert() public {
        vm.startPrank(userA);
        vault.depositETH{value: 1 ether}();
        vm.stopPrank();

        // Intent: 1 WETH -> >= 3000 USDC, partial fill NOT allowed
        UserIntent memory intentA = UserIntent({
            user: userA,
            tokenIn: address(weth),
            tokenOut: address(usdc),
            amountIn: 1 ether,
            minAmountOut: 3000 * 10**18,
            deadline: block.timestamp + 1 hours,
            salt: bytes32(uint256(1)),
            allowPartialFill: false
        });
        bytes memory sigA = _signIntent(intentA, userAPrivateKey);

        // Attempt partial (0.5 WETH) — should revert
        vm.startPrank(keeper);
        vm.expectRevert("Partial fill not allowed");
        treasury.forwardExecuteIntent(intentA, sigA, 2, abi.encode(1500 * 10**18), 0.5 ether);
        vm.stopPrank();
    }
}
