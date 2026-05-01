// SPDX-License-Identifier: MIT
pragma solidity ^0.8.25;

import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {EIP712} from "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

import {IntentVault, UserIntent} from "./IntentVault.sol";
import {DarkPoolOTC} from "./DarkPoolOTC.sol";

interface ITreasury {
    function treasurySwap(address tokenOut, address to, uint256 amount) external;
}

/// @title SettlementRouter
/// @notice Core settlement engine. Verifies EIP-712 signed intents and routes
///         execution through DEX, OTC dark pool, or treasury internalization.
contract SettlementRouter is EIP712, Ownable {
    using ECDSA for bytes32;
    using SafeERC20 for IERC20;

    IntentVault public vault;
    DarkPoolOTC public otcPool;
    address public uniswapRouter;
    address public treasury;

    /// @notice Tracks consumed amount per intent hash (supports partial fills).
    mapping(bytes32 => uint256) public filledAmountIn;

    bytes32 public constant INTENT_TYPEHASH = keccak256(
        "UserIntent(address user,address tokenIn,address tokenOut,uint256 amountIn,uint256 minAmountOut,uint256 deadline,bytes32 salt,bool allowPartialFill)"
    );

    event IntentExecuted(
        bytes32 indexed intentHash,
        address indexed user,
        uint8 actionType,
        uint256 executeAmountIn,
        uint256 actualAmountOut
    );

    constructor(address _vault, address _otcPool, address _uniswapRouter) EIP712("SettlementRouter", "1") Ownable(msg.sender) {
        vault = IntentVault(_vault);
        otcPool = DarkPoolOTC(_otcPool);
        uniswapRouter = _uniswapRouter;
    }

    function setUniswapRouter(address _router) external onlyOwner {
        require(_router != address(0), "Invalid router address");
        uniswapRouter = _router;
    }

    function setTreasury(address _treasury) external onlyOwner {
        require(_treasury != address(0), "Invalid treasury address");
        treasury = _treasury;
    }

    /// @notice Execute a signed user intent.
    /// @param intent      Off-chain user intent
    /// @param signature   EIP-712 ECDSA signature over the intent
    /// @param actionType  0 = DEX, 1 = OTC, 2 = Treasury
    /// @param executionData  AI-determined execution params (varies by actionType)
    /// @param executeAmountIn  Amount of tokenIn to consume in this execution
    function executeIntent(
        UserIntent calldata intent,
        bytes memory signature,
        uint8 actionType,
        bytes calldata executionData,
        uint256 executeAmountIn
    ) external {
        // Deadline check
        require(block.timestamp <= intent.deadline, "Intent expired");

        // Enforce full-fill if partial not allowed
        if (!intent.allowPartialFill) {
            require(executeAmountIn == intent.amountIn, "Partial fill not allowed");
        }

        // Anti-replay & remaining quota check
        bytes32 intentHash = hashIntent(intent);
        require(intent.amountIn - filledAmountIn[intentHash] >= executeAmountIn, "Overfill intent");

        // EIP-712 signature verification
        {
            bytes32 digest = _hashTypedDataV4(intentHash);
            address signer = ECDSA.recover(digest, signature);
            require(signer == intent.user, "Invalid signature");
        }

        // Mark consumed amount
        filledAmountIn[intentHash] += executeAmountIn;

        // Snapshot user's tokenOut balance for slippage check
        uint256 balanceBefore = IERC20(intent.tokenOut).balanceOf(intent.user);

        // Route to handler
        if (actionType == 0) {
            _executeDEX(intent, executionData, executeAmountIn);
        } else if (actionType == 1) {
            _executeOTC(intent, executionData, executeAmountIn);
        } else if (actionType == 2) {
            _executeTreasury(intent, executionData, executeAmountIn);
        } else {
            revert("Invalid actionType");
        }

        // Post-execution slippage guard (pro-rata for partial fills)
        uint256 requiredMinAmountOut = (intent.minAmountOut * executeAmountIn + intent.amountIn - 1) / intent.amountIn;
        uint256 balanceAfter = IERC20(intent.tokenOut).balanceOf(intent.user);
        uint256 actualAmountOut = balanceAfter - balanceBefore;
        require(actualAmountOut >= requiredMinAmountOut, "User A Slippage too high");

        emit IntentExecuted(intentHash, intent.user, actionType, executeAmountIn, actualAmountOut);
    }

    // ── ActionType 0: External DEX routing ──

    function _executeDEX(UserIntent calldata intent, bytes calldata executionData, uint256 executeAmountIn) internal {
        require(uniswapRouter != address(0), "Uniswap router not set");

        bytes memory dexData = abi.decode(executionData, (bytes));
        
        vault.routerTransfer(intent.user, intent.tokenIn, address(this), executeAmountIn);
        IERC20(intent.tokenIn).forceApprove(uniswapRouter, executeAmountIn);

        (bool success, bytes memory returnData) = uniswapRouter.call(dexData);
        if (!success) {
            if (returnData.length > 0) {
                assembly {
                    let returnData_size := mload(returnData)
                    revert(add(32, returnData), returnData_size)
                }
            } else {
                revert("Uniswap swap failed");
            }
        }

        // Revoke leftover approval
        IERC20(intent.tokenIn).forceApprove(uniswapRouter, 0);

        // Refund unconsumed tokenIn back to vault
        uint256 remainingTokenIn = IERC20(intent.tokenIn).balanceOf(address(this));
        if (remainingTokenIn > 0) {
            IERC20(intent.tokenIn).forceApprove(address(vault), remainingTokenIn);
            vault.refund(intent.user, intent.tokenIn, remainingTokenIn);
        }

        // Forward any tokenOut sitting in router to user
        uint256 routerBalanceOut = IERC20(intent.tokenOut).balanceOf(address(this));
        if (routerBalanceOut > 0) {
            IERC20(intent.tokenOut).safeTransfer(intent.user, routerBalanceOut);
        }
    }

    // ── ActionType 1: OTC dark pool matching ──

    function _executeOTC(UserIntent calldata intent, bytes calldata executionData, uint256 executeAmountIn) internal {
        (UserIntent memory intentB, bytes memory signatureB, uint256 executeAmountInB) = abi.decode(executionData, (UserIntent, bytes, uint256));
        
        // Validate intent B
        require(block.timestamp <= intentB.deadline, "Intent B expired");
        if (!intentB.allowPartialFill) {
            require(executeAmountInB == intentB.amountIn, "Partial fill not allowed for B");
        }
        bytes32 hashB = hashIntent(intentB);
        require(intentB.amountIn - filledAmountIn[hashB] >= executeAmountInB, "Overfill Intent B");
        
        {
            bytes32 digestB = _hashTypedDataV4(hashB);
            address signerB = ECDSA.recover(digestB, signatureB);
            require(signerB == intentB.user, "Invalid signature B");
        }
        
        filledAmountIn[hashB] += executeAmountInB;

        // Token pair must be inverse
        require(intent.tokenIn == intentB.tokenOut && intent.tokenOut == intentB.tokenIn, "Token mismatch");

        // Pro-rata min output checks
        uint256 requiredMinAmountOutB = (intentB.minAmountOut * executeAmountInB + intentB.amountIn - 1) / intentB.amountIn;
        uint256 requiredMinAmountOutA = (intent.minAmountOut * executeAmountIn + intent.amountIn - 1) / intent.amountIn;

        require(executeAmountIn >= requiredMinAmountOutB, "Amount B mismatch");
        require(executeAmountInB >= requiredMinAmountOutA, "Amount A mismatch");

        uint256 balanceBBefore = IERC20(intentB.tokenOut).balanceOf(intentB.user);

        // Transfer both sides to OTC pool and swap
        vault.routerTransfer(intent.user, intent.tokenIn, address(otcPool), executeAmountIn);
        vault.routerTransfer(intentB.user, intentB.tokenIn, address(otcPool), executeAmountInB);

        otcPool.executeAtomicSwap(
            intent.user, intent.tokenIn, executeAmountIn,
            intentB.user, intentB.tokenIn, executeAmountInB
        );

        // Slippage guard for user B
        uint256 balanceBAfter = IERC20(intentB.tokenOut).balanceOf(intentB.user);
        uint256 actualAmountOutB = balanceBAfter - balanceBBefore;
        require(actualAmountOutB >= requiredMinAmountOutB, "User B Slippage too high");
        
        emit IntentExecuted(hashB, intentB.user, 1, executeAmountInB, actualAmountOutB);
    }

    // ── ActionType 2: Treasury internalization ──

    function _executeTreasury(UserIntent calldata intent, bytes calldata executionData, uint256 executeAmountIn) internal {
        require(treasury != address(0), "Treasury not set");
        uint256 treasuryAmountOut = abi.decode(executionData, (uint256));

        // Send user's tokenIn to treasury, receive tokenOut from treasury
        vault.routerTransfer(intent.user, intent.tokenIn, treasury, executeAmountIn);
        ITreasury(treasury).treasurySwap(intent.tokenOut, intent.user, treasuryAmountOut);
    }

    /// @notice Compute EIP-712 struct hash for an intent.
    function hashIntent(UserIntent memory intent) public pure returns (bytes32) {
        return keccak256(
            abi.encode(
                INTENT_TYPEHASH,
                intent.user,
                intent.tokenIn,
                intent.tokenOut,
                intent.amountIn,
                intent.minAmountOut,
                intent.deadline,
                intent.salt,
                intent.allowPartialFill
            )
        );
    }
}
